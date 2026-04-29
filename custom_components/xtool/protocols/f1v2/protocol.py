"""F1 V2 protocol (xTool F1 firmware 40.51+).

Listener-only TLS WebSocket on port 28900. The device pushes JSON events
(both as TEXT and BINARY frames) describing state changes. Writes to the
device are not implemented — this protocol only feeds the read-only
sensors and binary sensors.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import uuid
from typing import Any

import aiohttp

from ...const import XtoolStatus
from ..base import (
    DeviceInfo,
    XtoolDeviceState,
    XtoolProtocol,
)


# --- F1 V2 (firmware 40.51+) ------------------------------------------------
# Listener-only WebSocket on a custom TLS endpoint.

F1V2_WS_PORT = 28900
F1V2_WS_PATH = "/websocket"
F1V2_WS_HANDSHAKE = "bWFrZWJsb2NrLXh0b29s"  # base64 of "makeblock-xtool"
F1V2_WS_PING = b"\xc0\x00"
F1V2_HEARTBEAT_SECONDS = 2.0
F1V2_PROBE_TIMEOUT = 5.0

# /work/mode info.mode value → XtoolStatus
F1V2_MODE_MAP: dict[str, XtoolStatus] = {
    "P_SLEEP": XtoolStatus.SLEEPING,
    "P_WORK": XtoolStatus.IDLE,
    "P_ONLINE_READY_WORK": XtoolStatus.PROCESSING_READY,
    "P_OFFLINE_READY_WORK": XtoolStatus.PROCESSING_READY,
    "P_READY": XtoolStatus.PROCESSING_READY,
    "P_WORKING": XtoolStatus.PROCESSING,
    "P_WORK_DONE": XtoolStatus.FINISHED,
    "P_FINISH": XtoolStatus.FINISHED,
    "P_ERROR": XtoolStatus.ERROR_LIMIT,
}

_LOGGER = logging.getLogger(__name__)


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class F1V2Protocol(XtoolProtocol):
    """xTool F1 V2 (firmware 40.51+) listener-only protocol."""

    def __init__(self, host: str, port: int = F1V2_WS_PORT) -> None:
        super().__init__(host)
        self._port = port
        self._task: asyncio.Task[None] | None = None
        self._latest: dict[str, Any] = {}
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._listen_loop())

    async def disconnect(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._connected = False

    async def get_version(self) -> str | None:
        return self._latest.get("firmware_version")

    async def get_device_info(self) -> DeviceInfo:
        info = DeviceInfo()
        info.device_name = self._latest.get("device_name", "xTool F1")
        info.serial_number = self._latest.get("serial_number", "")
        info.main_firmware = self._latest.get("firmware_version", "")
        return info

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Copy the listener's latest snapshot into the shared state."""
        if not self._connected:
            raise ConnectionError("F1 V2 WebSocket not connected")

        status = self._latest.get("status")
        if status is not None:
            state.status = status

        if "cover_open" in self._latest:
            state.cover_open = self._latest["cover_open"]
        if "machine_lock" in self._latest:
            state.machine_lock = self._latest["machine_lock"]
        if "task_id" in self._latest:
            state.task_id = str(self._latest["task_id"])
        if "task_time" in self._latest:
            try:
                state.task_time = int(self._latest["task_time"])
            except (TypeError, ValueError):
                pass

        for key in (
            "flame_alarm_v2_enabled",
            "beep_enabled_v2",
            "gap_check_enabled",
            "machine_lock_check_enabled",
            "purifier_timeout",
            "working_mode",
            "last_button_event",
            "last_job_time_seconds",
        ):
            if key in self._latest:
                setattr(state, key, self._latest[key])

    async def validate(self):  # type: ignore[override]
        """Probe wss endpoint with handshake; ConnectionInfo on first frame."""
        url = (
            f"wss://{self.host}:{self._port}{F1V2_WS_PATH}"
            f"?id={uuid.uuid4()}&function=instruction"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    url,
                    ssl=_ssl_context(),
                    timeout=aiohttp.ClientTimeout(total=F1V2_PROBE_TIMEOUT),
                    heartbeat=None,
                    max_msg_size=0,
                ) as ws:
                    await ws.send_str(F1V2_WS_HANDSHAKE)
                    try:
                        msg = await asyncio.wait_for(
                            ws.receive(), timeout=F1V2_PROBE_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        return None
                    if msg.type not in (
                        aiohttp.WSMsgType.TEXT,
                        aiohttp.WSMsgType.BINARY,
                    ):
                        return None
        except Exception as err:
            _LOGGER.debug("F1 V2 probe failed for %s: %s", self.host, err)
            return None

        # Probe succeeded — return ConnectionInfo with placeholders; real
        # device info arrives over the listener once connect() is called.
        from ..base import ConnectionInfo

        return ConnectionInfo(
            host=self.host,
            name="xTool F1 V2",
            serial_number="",
            firmware_version="",
            laser_power_watts=0,
            device_info=DeviceInfo(),
        )

    # --- Listener implementation ---

    async def _listen_loop(self) -> None:
        url = (
            f"wss://{self.host}:{self._port}{F1V2_WS_PATH}"
            f"?id={uuid.uuid4()}&function=instruction"
        )
        backoff = 5.0
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        url,
                        ssl=_ssl_context(),
                        timeout=aiohttp.ClientTimeout(total=15),
                        heartbeat=None,
                        max_msg_size=0,
                    ) as ws:
                        await ws.send_str(F1V2_WS_HANDSHAKE)
                        self._connected = True
                        backoff = 5.0
                        ping_task = asyncio.create_task(self._heartbeat(ws))
                        try:
                            async for msg in ws:
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    self._handle_text(msg.data)
                                elif msg.type == aiohttp.WSMsgType.BINARY:
                                    self._handle_binary(msg.data)
                                elif msg.type in (
                                    aiohttp.WSMsgType.CLOSED,
                                    aiohttp.WSMsgType.ERROR,
                                ):
                                    break
                        finally:
                            ping_task.cancel()
                            try:
                                await ping_task
                            except (asyncio.CancelledError, Exception):
                                pass
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug("F1 V2 WS error (%s): retrying in %ss", err, backoff)
            self._connected = False
            await asyncio.sleep(backoff)

    async def _heartbeat(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            await asyncio.sleep(F1V2_HEARTBEAT_SECONDS)
            try:
                await ws.send_bytes(F1V2_WS_PING)
            except Exception:
                return

    def _handle_text(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except Exception:
            return
        self._dispatch(event)

    def _handle_binary(self, raw: bytes) -> None:
        idx = raw.find(b"{")
        if idx < 0:
            return
        payload = raw[idx:]
        # Some F1 V2 frames duplicate the opening brace.
        if payload.startswith(b"{{"):
            payload = payload[1:]
        try:
            event = json.loads(payload.decode("utf-8"))
        except Exception:
            return
        self._dispatch(event)

    def _dispatch(self, event: dict[str, Any]) -> None:
        url = event.get("url")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        module = data.get("module")
        typ = data.get("type")
        info = data.get("info")

        if url == "/work/mode" and module == "STATUS_CONTROLLER" and typ == "MODE_CHANGE":
            mode = str(info.get("mode") if isinstance(info, dict) else "").upper()
            mapped = F1V2_MODE_MAP.get(mode)
            if mapped is not None:
                self._latest["status"] = mapped
            if isinstance(info, dict) and info.get("taskId") is not None:
                self._latest["task_id"] = info["taskId"]

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
                        self._latest["task_time"] = int(info["timeUse"]) // 1000
                        self._latest["last_job_time_seconds"] = (
                            int(info["timeUse"]) // 1000
                        )
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
            # Per BassXT: device emits OPEN when unlocked, CLOSE when locked.
            if typ == "OPEN":
                self._latest["machine_lock"] = False
            elif typ == "CLOSE":
                self._latest["machine_lock"] = True

        elif url == "/device/config" and module == "DEVICE_CONFIG" and typ == "INFO":
            if isinstance(info, dict):
                if "flameAlarm" in info:
                    self._latest["flame_alarm_v2_enabled"] = bool(info["flameAlarm"])
                if "beepEnable" in info:
                    self._latest["beep_enabled_v2"] = bool(info["beepEnable"])
                if "gapCheck" in info:
                    self._latest["gap_check_enabled"] = bool(info["gapCheck"])
                if "machineLockCheck" in info:
                    self._latest["machine_lock_check_enabled"] = bool(
                        info["machineLockCheck"]
                    )
                if "purifierTimeout" in info:
                    try:
                        self._latest["purifier_timeout"] = int(info["purifierTimeout"])
                    except (TypeError, ValueError):
                        pass
                if "workingMode" in info:
                    self._latest["working_mode"] = str(info["workingMode"])

        elif url == "/button/status" and module == "BUTTON":
            self._latest["last_button_event"] = f"{typ}:{info}" if info else str(typ)

        else:
            _LOGGER.debug("F1 V2 unhandled event: %s", event)


