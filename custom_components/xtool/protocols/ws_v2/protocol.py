"""xTool V2 protocol — TLS WebSocket-tunneled REST API.

The V2 firmware on F1 (≥40.51), F1 Ultra V2, F2 family, M1 Ultra, P2S,
P3, MetalFab and Apparel Printer replaces the legacy port-8080 HTTP REST
transport with a TLS WebSocket on port 28900 carrying a JSON
request/response API plus unsolicited push events. The same wire
protocol is used by all V2 devices — only the device's firmware bundle
varies.

Three concurrent WebSocket connections share the same `id` (timestamp)
query parameter:

- ``function=instruction`` — JSON request/response API + broadcast push
  events.
- ``function=file_stream`` — binary file uploads (G-code, firmware
  package, log archives).
- ``function=media_stream`` — camera / live-preview frames.

Frame envelopes (best-effort reverse-engineered from the xTool Studio
v3.70.90 extension bundles; the live wire format is not publicly
documented):

Request::

    {
      "url": "/v1/device/runtime-infos",
      "method": "GET",
      "requestId": "<unique>",
      "params": {...},
      "data": {...}
    }

Response::

    {
      "requestId": "<same>",
      "code": 0,
      "data": <object>,
      "msg": "ok"
    }

Push event::

    {
      "url": "/work/mode",
      "data": {"module": "STATUS_CONTROLLER",
                "type":   "MODE_CHANGE",
                "info":   {...}},
      "timestamp": 1700000000000
    }

If the live wire shape diverges from these assumptions, override
``_encode_request`` / ``_decode_response`` rather than rewriting the
loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
import uuid
from collections.abc import Callable
from typing import Any

import aiohttp

from ...const import XtoolStatus
from ..base import (
    DeviceInfo,
    FirmwareFile,
    LaserInfo,
    XtoolDeviceState,
    XtoolProtocol,
)


WSV2_PORT = 28900
WSV2_PATH = "/websocket"
WSV2_HEARTBEAT_SECONDS = 5.0
WSV2_REQUEST_TIMEOUT = 8.0
WSV2_PROBE_TIMEOUT = 4.0
WSV2_FILE_UPLOAD_TIMEOUT = 600.0

# Mapping of V2 work-mode strings to the integration's normalised status enum.
# Sources: xTool Studio bundle ``/work/mode`` push handler + the
# ``/v1/device/runtime-infos`` `curMode.mode` response field.
WSV2_MODE_MAP: dict[str, XtoolStatus] = {
    "P_BOOT":               XtoolStatus.INITIALIZING,
    "P_SLEEP":              XtoolStatus.SLEEPING,
    "P_IDLE":               XtoolStatus.IDLE,
    "P_READY":              XtoolStatus.PROCESSING_READY,
    "P_WORK":               XtoolStatus.IDLE,
    "P_ONLINE_READY_WORK":  XtoolStatus.PROCESSING_READY,
    "P_OFFLINE_READY_WORK": XtoolStatus.PROCESSING_READY,
    "P_WORKING":            XtoolStatus.PROCESSING,
    "P_WORK_DONE":          XtoolStatus.FINISHED,
    "P_FINISH":             XtoolStatus.FINISHED,
    "P_MEASURE":            XtoolStatus.MEASURING,
    "P_UPGRADE":            XtoolStatus.FIRMWARE_UPDATE,
    "P_ERROR":              XtoolStatus.ERROR_LIMIT,
}


_LOGGER = logging.getLogger(__name__)


def _ssl_context() -> ssl.SSLContext:
    """SSL context that accepts self-signed device certs."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def probe_v2(host: str, timeout: float = WSV2_PROBE_TIMEOUT) -> bool:
    """Quick port probe to detect whether a host speaks V2.

    Opens a TLS WebSocket to ``wss://<host>:28900/websocket?…&function=instruction``
    and waits for any frame. Returns True on success, False on any error
    or timeout. No state is held — callers create a real protocol instance
    afterwards.
    """
    url = (
        f"wss://{host}:{WSV2_PORT}{WSV2_PATH}"
        f"?id={int(time.time() * 1000)}&function=instruction"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url,
                ssl=_ssl_context(),
                timeout=aiohttp.ClientTimeout(total=timeout),
                heartbeat=None,
                max_msg_size=0,
            ) as ws:
                # Some V2 firmware emits a hello frame within ~1 s; some
                # only respond to a request. Either signal counts.
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
                except asyncio.TimeoutError:
                    # Connection succeeded but no frame within window.
                    # Treat as "probably V2" — we successfully opened the
                    # TLS WS, which V1 devices (HTTP-only port 8080) can't
                    # do.
                    return True
                return msg.type in (
                    aiohttp.WSMsgType.TEXT,
                    aiohttp.WSMsgType.BINARY,
                )
    except Exception as err:
        _LOGGER.debug("V2 probe failed for %s: %s", host, err)
        return False


class _PendingRequest:
    """One in-flight request awaiting its response by ``requestId``."""

    __slots__ = ("future", "deadline")

    def __init__(self, deadline: float) -> None:
        self.future: asyncio.Future[dict[str, Any]] = asyncio.Future()
        self.deadline = deadline


class WSV2Protocol(XtoolProtocol):
    """V2 protocol — TLS WS request/response + push events.

    Owns the ``function=instruction`` connection and a lazy
    ``function=file_stream`` connection used during firmware/G-code
    uploads. Push events update ``self._latest`` in place; explicit
    queries (``poll_state``) issue a `GET` request and update ``state``
    from the JSON reply.
    """

    def __init__(self, host: str, port: int = WSV2_PORT) -> None:
        super().__init__(host)
        self._port = port
        self._session: aiohttp.ClientSession | None = None
        self._ws_instr: aiohttp.ClientWebSocketResponse | None = None
        self._ws_file: aiohttp.ClientWebSocketResponse | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._pending: dict[str, _PendingRequest] = {}
        self._latest: dict[str, Any] = {}
        self._connected = False
        self._connect_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    # ── connection lifecycle ──────────────────────────────────────────

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._connected and self._ws_instr and not self._ws_instr.closed:
                return
            await self._open_instruction_ws()

    async def _open_instruction_ws(self) -> None:
        url = (
            f"wss://{self.host}:{self._port}{WSV2_PATH}"
            f"?id={int(time.time() * 1000)}&function=instruction"
        )
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        try:
            self._ws_instr = await self._session.ws_connect(
                url,
                ssl=_ssl_context(),
                timeout=aiohttp.ClientTimeout(total=15),
                heartbeat=None,
                max_msg_size=0,
            )
        except Exception as err:
            _LOGGER.debug("V2 connect failed for %s: %s", self.host, err)
            await self._close_quiet()
            raise ConnectionError(f"V2 WebSocket connect failed: {err}") from err
        self._connected = True
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def disconnect(self) -> None:
        await self._close_quiet()

    async def _close_quiet(self) -> None:
        self._connected = False
        for t in (self._reader_task, self._heartbeat_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._reader_task = None
        self._heartbeat_task = None
        for ws in (self._ws_instr, self._ws_file):
            if ws and not ws.closed:
                try:
                    await ws.close()
                except Exception:
                    pass
        self._ws_instr = None
        self._ws_file = None
        # Resolve any outstanding requests so callers don't hang.
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(
                    ConnectionError("V2 WebSocket closed")
                )
        self._pending.clear()
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None

    # ── request/response API ──────────────────────────────────────────

    async def request(
        self,
        url: str,
        method: str = "GET",
        params: dict | None = None,
        data: dict | None = None,
        timeout: float = WSV2_REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a JSON request frame, await the matching response.

        Returns the parsed ``data`` field from the response. Raises
        ``ConnectionError`` on disconnect, ``asyncio.TimeoutError`` on
        timeout, ``RuntimeError`` on non-zero ``code``.
        """
        if not self._connected or self._ws_instr is None:
            await self.connect()
        if self._ws_instr is None:
            raise ConnectionError("V2 WebSocket not connected")
        request_id = uuid.uuid4().hex
        payload: dict[str, Any] = {
            "url": url,
            "method": method.upper(),
            "requestId": request_id,
        }
        if params is not None:
            payload["params"] = params
        if data is not None:
            payload["data"] = data
        deadline = time.monotonic() + timeout
        pending = _PendingRequest(deadline)
        self._pending[request_id] = pending
        try:
            await self._ws_instr.send_str(json.dumps(payload))
        except Exception as err:
            self._pending.pop(request_id, None)
            raise ConnectionError(f"V2 send failed: {err}") from err
        try:
            response = await asyncio.wait_for(pending.future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise
        code = response.get("code", 0) if isinstance(response, dict) else 0
        if code != 0:
            msg = response.get("msg") or response.get("message") or "unknown"
            raise RuntimeError(
                f"V2 {method} {url} returned code {code}: {msg}"
            )
        return (
            response.get("data") if isinstance(response, dict) else {}
        ) or {}

    # ── reader / heartbeat loops ──────────────────────────────────────

    async def _reader_loop(self) -> None:
        ws = self._ws_instr
        if ws is None:
            return
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._handle_frame(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    self._handle_binary(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("V2 reader loop exited: %s", err)
        finally:
            self._connected = False

    async def _heartbeat_loop(self) -> None:
        try:
            while self._connected:
                await asyncio.sleep(WSV2_HEARTBEAT_SECONDS)
                if self._ws_instr is None or self._ws_instr.closed:
                    return
                try:
                    # V2 heartbeat: empty JSON ping ({}). Some firmware
                    # accepts a `{"type":"ping"}` frame instead — both
                    # have been observed in xTool Studio bundle traces.
                    await self._ws_instr.send_str('{"type":"ping"}')
                except Exception as err:
                    _LOGGER.debug("V2 heartbeat failed: %s", err)
                    return
        except asyncio.CancelledError:
            raise

    def _handle_frame(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except Exception:
            return
        if not isinstance(event, dict):
            return
        request_id = event.get("requestId")
        if request_id and request_id in self._pending:
            pending = self._pending.pop(request_id)
            if not pending.future.done():
                pending.future.set_result(event)
            return
        # Otherwise treat as a push event.
        self._dispatch_push(event)

    def _handle_binary(self, raw: bytes) -> None:
        idx = raw.find(b"{")
        if idx < 0:
            return
        payload = raw[idx:]
        if payload.startswith(b"{{"):
            payload = payload[1:]
        try:
            text = payload.decode("utf-8", errors="replace")
        except Exception:
            return
        self._handle_frame(text)

    # ── push event dispatcher ─────────────────────────────────────────

    def _dispatch_push(self, event: dict[str, Any]) -> None:
        url = event.get("url") or ""
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        module = data.get("module")
        typ = data.get("type")
        info = data.get("info")

        if url == "/work/mode" and module == "STATUS_CONTROLLER" and typ == "MODE_CHANGE":
            mode = ""
            if isinstance(info, dict):
                mode = str(info.get("mode") or "").upper()
                if info.get("taskId") is not None:
                    self._latest["task_id"] = info["taskId"]
            mapped = WSV2_MODE_MAP.get(mode)
            if mapped is not None:
                self._latest["status"] = mapped

        elif url == "/device/status" and module == "STATUS_CONTROLLER":
            info_str = str(info).lower() if info is not None else ""
            if typ == "WORK_PREPARED":
                self._latest["status"] = (
                    XtoolStatus.FRAMING if info_str == "framing"
                    else XtoolStatus.PROCESSING_READY
                )
            elif typ == "WORK_STARTED":
                self._latest["status"] = (
                    XtoolStatus.FRAMING if info_str == "framing"
                    else XtoolStatus.PROCESSING
                )
            elif typ == "WORK_FINISHED":
                self._latest["status"] = (
                    XtoolStatus.IDLE if info_str == "framing"
                    else XtoolStatus.FINISHED
                )

        elif url == "/work/result" and module == "WORK_RESULT" and typ == "WORK_FINISHED":
            self._latest["status"] = XtoolStatus.FINISHED
            if isinstance(info, dict):
                if info.get("timeUse") is not None:
                    try:
                        secs = int(info["timeUse"]) // 1000
                        self._latest["task_time"] = secs
                        self._latest["last_job_time_seconds"] = secs
                    except (TypeError, ValueError):
                        pass
                if info.get("taskId") is not None:
                    self._latest["task_id"] = info["taskId"]

        elif url == "/gap/status" and module == "GAP":
            if typ == "OPEN":
                self._latest["cover_open"] = True
            elif typ == "CLOSE":
                self._latest["cover_open"] = False

        elif url == "/machine_lock/status" and module == "MACHINE_LOCK":
            # Device emits OPEN when unlocked, CLOSE when locked.
            if typ == "OPEN":
                self._latest["machine_lock"] = False
            elif typ == "CLOSE":
                self._latest["machine_lock"] = True

        elif url == "/button/status" and module == "BUTTON":
            self._latest["last_button_event"] = (
                f"{typ}:{info}" if info else str(typ)
            )

        else:
            _LOGGER.debug("V2 unhandled push event: %s", event)

    # ── XtoolProtocol contract ────────────────────────────────────────

    async def get_version(self) -> str | None:
        try:
            data = await self.request("/v1/device/machineInfo", "GET")
        except Exception:
            return self._latest.get("firmware_version")
        firmware = data.get("firmware") if isinstance(data, dict) else None
        if isinstance(firmware, dict):
            ver = (
                firmware.get("package_version")
                or firmware.get("master_h3_laserservice")
                or ""
            )
            self._latest["firmware_version"] = ver
            return ver or None
        return None

    async def get_device_info(self) -> DeviceInfo:
        info = DeviceInfo()
        try:
            data = await self.request("/v1/device/machineInfo", "GET")
        except Exception:
            data = {}
        if isinstance(data, dict):
            info.device_name = (
                data.get("deviceName") or data.get("machine_name") or ""
            )
            info.serial_number = data.get("sn") or data.get("snCode") or ""
            info.mac_address = data.get("mac", "")
            firmware = data.get("firmware") or {}
            if isinstance(firmware, dict):
                info.main_firmware = (
                    firmware.get("package_version")
                    or firmware.get("master_h3_laserservice")
                    or ""
                )
            laser_power = data.get("laserPower")
            if isinstance(laser_power, list) and laser_power:
                try:
                    watts = int(laser_power[0])
                except (TypeError, ValueError):
                    watts = 0
                info.laser_power_watts = watts
                info.laser = LaserInfo(power_watts=watts)
        # Cache for later poll cycles
        self._latest["device_name"] = info.device_name
        self._latest["serial_number"] = info.serial_number
        self._latest["firmware_version"] = info.main_firmware
        return info

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Refresh state via V2 request endpoints + push cache."""
        if not self._connected:
            await self.connect()

        # 1. Runtime state — work mode + subMode + taskId
        try:
            rt = await self.request("/v1/device/runtime-infos", "GET")
        except Exception as err:
            _LOGGER.debug("V2 runtime-infos failed: %s", err)
            rt = {}
        if isinstance(rt, dict):
            cur_mode = rt.get("curMode") or {}
            if isinstance(cur_mode, dict):
                mode = str(cur_mode.get("mode") or "").upper()
                mapped = WSV2_MODE_MAP.get(mode)
                if mapped is not None:
                    state.status = mapped
                    self._latest["status"] = mapped
                if cur_mode.get("subMode"):
                    state.working_mode = str(cur_mode["subMode"])
                if cur_mode.get("taskId"):
                    state.task_id = str(cur_mode["taskId"])

        # 2. Peripheral aggregate via /v1/peripheral/param?type=...
        for ptype, target_attr in (
            ("ext_purifier", "purifier_state_raw"),
            ("gap", "cover_open"),
            ("machine_lock", "machine_lock"),
            ("airassistV2", "air_assist_connected"),
        ):
            try:
                p = await self.request(
                    "/v1/peripheral/param", "GET",
                    params={"type": ptype},
                )
            except Exception:
                continue
            if not isinstance(p, dict):
                continue
            if ptype == "gap":
                state.cover_open = p.get("state") == "off"
            elif ptype == "machine_lock":
                # state "on" = locked, "off" = unlocked. The HA LOCK
                # device class wants `True` = unlocked.
                state.machine_lock = p.get("state") == "off"
            elif ptype == "airassistV2":
                state.air_assist_connected = p.get("state") == "on"

        # 3. Push-cached values (overrule poll if newer)
        for k in (
            "status", "task_id", "task_time", "cover_open", "machine_lock",
            "last_button_event", "last_job_time_seconds",
        ):
            if k in self._latest:
                setattr(state, k, self._latest[k])

    # ── firmware flash (V2 three-step) ─────────────────────────────────

    async def flash_firmware(
        self,
        files: list[FirmwareFile],
        blobs: list[bytes],
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """Three-step V2 flash via instruction WS + file_stream WS."""
        if not files or not blobs:
            raise RuntimeError("V2 flash: empty file list")
        machine_type = getattr(self, "_pending_machine_type", "") or "MXF"

        # 1. handshake — ready
        try:
            await self.request(
                "/v1/device/upgrade-mode",
                "PUT",
                params={"mode": "ready"},
                data={"machine_type": machine_type},
                timeout=60,
            )
        except Exception as err:
            raise RuntimeError(f"V2 firmware handshake failed: {err}") from err
        if progress_cb is not None:
            progress_cb(0.05)

        # 2. push the blob over file_stream
        await self._upload_file_stream(
            blobs[0],
            file_type=2,
            file_name="package.img",
            progress_cb=lambda f: progress_cb(0.05 + f * 0.85)
            if progress_cb else None,
        )
        if progress_cb is not None:
            progress_cb(0.9)

        # 3. trigger the burn
        try:
            await self.request(
                "/v1/device/upgrade-mode",
                "PUT",
                params={"mode": "upgrade"},
                data={"force_upgrade": 1, "action": "burn", "atomm": 1},
                timeout=120,
            )
        except Exception as err:
            raise RuntimeError(f"V2 firmware trigger failed: {err}") from err
        if progress_cb is not None:
            progress_cb(1.0)

    async def _upload_file_stream(
        self,
        blob: bytes,
        file_type: int,
        file_name: str,
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """Open the file_stream WS, push a binary blob, close.

        Best-effort framing: opens a fresh ``function=file_stream`` WS,
        sends a JSON descriptor first (``{"fileType":..,"fileName":..}``)
        then the raw bytes as a single BINARY frame. The exact
        chunk-size + ack handshake the real device expects is not
        documented; this mirrors how axios-over-WS schedules a
        ``isFileTransfer:true`` request.
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        url = (
            f"wss://{self.host}:{self._port}{WSV2_PATH}"
            f"?id={int(time.time() * 1000)}&function=file_stream"
        )
        async with self._session.ws_connect(
            url,
            ssl=_ssl_context(),
            timeout=aiohttp.ClientTimeout(total=WSV2_FILE_UPLOAD_TIMEOUT),
            heartbeat=None,
            max_msg_size=0,
        ) as ws:
            descriptor = {
                "fileType": file_type,
                "fileName": file_name,
                "fileSize": len(blob),
            }
            await ws.send_str(json.dumps(descriptor))
            # Stream the blob in 64 KiB chunks so the progress callback
            # ticks while the upload is in flight.
            chunk_size = 64 * 1024
            sent = 0
            while sent < len(blob):
                chunk = blob[sent:sent + chunk_size]
                await ws.send_bytes(chunk)
                sent += len(chunk)
                if progress_cb is not None and len(blob) > 0:
                    progress_cb(min(sent / len(blob), 1.0))
            # Closing JSON marker so the server knows the stream is done.
            await ws.send_str(json.dumps({"transferFinish": True}))
            try:
                ack = await asyncio.wait_for(ws.receive(), timeout=15)
                if ack.type == aiohttp.WSMsgType.TEXT:
                    _LOGGER.debug("V2 file_stream ack: %s", ack.data)
            except asyncio.TimeoutError:
                _LOGGER.debug("V2 file_stream: no ack received (timeout)")

    def set_machine_type(self, machine_type: str) -> None:
        """Stash the model's firmware_machine_type for the next flash call."""
        self._pending_machine_type = machine_type

    # The REST-family hook is stubbed here so coordinators that share a
    # base coordinator class don't crash when calling it on a V2 device.
    def set_strategy(self, strategy: str) -> None:  # pragma: no cover
        self._pending_strategy = strategy

    def set_model(self, model) -> None:  # pragma: no cover
        self._pending_model = model
