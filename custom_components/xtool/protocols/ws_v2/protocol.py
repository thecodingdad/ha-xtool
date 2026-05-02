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
from datetime import datetime
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
# Studio defaults observed in atomm-sharedworker.esm.*.js. Heartbeat
# interval matches the worker's ``heartbeatInterval ?? 3e3``; pong
# timeout matches ``heartbeatTimeout ?? 11e3``.
WSV2_HEARTBEAT_SECONDS = 3.0
WSV2_HEARTBEAT_TIMEOUT = 11.0
# Studio's CommandExecutor uses ``commandTimeout ?? 1e4`` = 10 s as
# the default for every execCmd call (including parity). Matching this
# avoids cutting off slow first-responses while the device's TLS
# stack + parity validator warm up.
WSV2_REQUEST_TIMEOUT = 10.0
WSV2_PROBE_TIMEOUT = 4.0
WSV2_FILE_UPLOAD_TIMEOUT = 600.0
# First-message handshake. Studio's worker stamps every connection with
# this ``socketFirstMessageCode`` (default ``bWFrZWJsb2NrLXh0b29s`` =
# base64 of ``makeblock-xtool``) inside a /v1/user/parity request and
# closes the WS if it isn't acknowledged. ``userUuid`` defaults to
# ``mk-guest`` for guest sessions.
WSV2_FIRST_MESSAGE_USER_KEY = "bWFrZWJsb2NrLXh0b29s"
WSV2_USER_UUID = "mk-guest"
WSV2_FIRST_MESSAGE_TIMEOUT = 10.0
# Heartbeat ping uses a fixed transactionId (0xFFE6 in Studio's
# HEART_MESSAGE_ID constant) so the user-request rotation can skip it.
WSV2_PING_TRANSACTION_ID = 65510
# Studio's generateTransactionId rotates a uint16-ish counter — we
# wrap below the ping id to keep the two pools disjoint.
WSV2_TRANSACTION_ID_WRAP = 65500

# Frame-format constants (Studio's MessageEncoder.encodeFrame /
# MessageParser.extractCompletePackets, both gated by
# ``dataStream: true``). All V2 instruction-channel traffic is wrapped
# in a 10-byte CRC-protected envelope; raw JSON over a TEXT frame is
# silently dropped by the device.
WSV2_FRAME_HEADER = bytes([0xBA, 0xBE])
WSV2_PROTOCOL_JSON = 4
WSV2_PROTOCOL_BUFFER = 5
# CRC-16/ARC (poly 0x8005 reflected, init 0, no xorout) — table-driven
# implementation matching Studio's ``crc16_default``.
_CRC16_TABLE = [
    0x0000, 0xC0C1, 0xC181, 0x0140, 0xC301, 0x03C0, 0x0280, 0xC241,
    0xC601, 0x06C0, 0x0780, 0xC741, 0x0500, 0xC5C1, 0xC481, 0x0440,
    0xCC01, 0x0CC0, 0x0D80, 0xCD41, 0x0F00, 0xCFC1, 0xCE81, 0x0E40,
    0x0A00, 0xCAC1, 0xCB81, 0x0B40, 0xC901, 0x09C0, 0x0880, 0xC841,
    0xD801, 0x18C0, 0x1980, 0xD941, 0x1B00, 0xDBC1, 0xDA81, 0x1A40,
    0x1E00, 0xDEC1, 0xDF81, 0x1F40, 0xDD01, 0x1DC0, 0x1C80, 0xDC41,
    0x1400, 0xD4C1, 0xD581, 0x1540, 0xD701, 0x17C0, 0x1680, 0xD641,
    0xD201, 0x12C0, 0x1380, 0xD341, 0x1100, 0xD1C1, 0xD081, 0x1040,
    0xF001, 0x30C0, 0x3180, 0xF141, 0x3300, 0xF3C1, 0xF281, 0x3240,
    0x3600, 0xF6C1, 0xF781, 0x3740, 0xF501, 0x35C0, 0x3480, 0xF441,
    0x3C00, 0xFCC1, 0xFD81, 0x3D40, 0xFF01, 0x3FC0, 0x3E80, 0xFE41,
    0xFA01, 0x3AC0, 0x3B80, 0xFB41, 0x3900, 0xF9C1, 0xF881, 0x3840,
    0x2800, 0xE8C1, 0xE981, 0x2940, 0xEB01, 0x2BC0, 0x2A80, 0xEA41,
    0xEE01, 0x2EC0, 0x2F80, 0xEF41, 0x2D00, 0xEDC1, 0xEC81, 0x2C40,
    0xE401, 0x24C0, 0x2580, 0xE541, 0x2700, 0xE7C1, 0xE681, 0x2640,
    0x2200, 0xE2C1, 0xE381, 0x2340, 0xE101, 0x21C0, 0x2080, 0xE041,
    0xA001, 0x60C0, 0x6180, 0xA141, 0x6300, 0xA3C1, 0xA281, 0x6240,
    0x6600, 0xA6C1, 0xA781, 0x6740, 0xA501, 0x65C0, 0x6480, 0xA441,
    0x6C00, 0xACC1, 0xAD81, 0x6D40, 0xAF01, 0x6FC0, 0x6E80, 0xAE41,
    0xAA01, 0x6AC0, 0x6B80, 0xAB41, 0x6900, 0xA9C1, 0xA881, 0x6840,
    0x7800, 0xB8C1, 0xB981, 0x7940, 0xBB01, 0x7BC0, 0x7A80, 0xBA41,
    0xBE01, 0x7EC0, 0x7F80, 0xBF41, 0x7D00, 0xBDC1, 0xBC81, 0x7C40,
    0xB401, 0x74C0, 0x7580, 0xB541, 0x7700, 0xB7C1, 0xB681, 0x7640,
    0x7200, 0xB2C1, 0xB381, 0x7340, 0xB101, 0x71C0, 0x7080, 0xB041,
    0x5000, 0x90C1, 0x9181, 0x5140, 0x9301, 0x53C0, 0x5280, 0x9241,
    0x9601, 0x56C0, 0x5780, 0x9741, 0x5500, 0x95C1, 0x9481, 0x5440,
    0x9C01, 0x5CC0, 0x5D80, 0x9D41, 0x5F00, 0x9FC1, 0x9E81, 0x5E40,
    0x5A00, 0x9AC1, 0x9B81, 0x5B40, 0x9901, 0x59C0, 0x5880, 0x9841,
    0x8801, 0x48C0, 0x4980, 0x8941, 0x4B00, 0x8BC1, 0x8A81, 0x4A40,
    0x4E00, 0x8EC1, 0x8F81, 0x4F40, 0x8D01, 0x4DC0, 0x4C80, 0x8C41,
    0x4400, 0x84C1, 0x8581, 0x4540, 0x8701, 0x47C0, 0x4680, 0x8641,
    0x8201, 0x42C0, 0x4380, 0x8341, 0x4100, 0x81C1, 0x8081, 0x4040,
]


def _json_compact(obj: Any) -> str:
    """Serialize JSON the same way ``JSON.stringify`` does — no spaces.

    Studio's ``execCmd`` calls ``JSON.stringify(payload)`` which yields
    a compact form (`{"a":1,"b":2}`). Python's default ``json.dumps``
    inserts spaces around separators. The bytes on the wire matter
    because:

    - The frame's payload-CRC is computed over our exact bytes;
    - V2 firmware's JSON parser is unknown — match Studio byte-for-byte
      to remove one variable from the protocol-handshake hunt.
    """
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = (_CRC16_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)) & 0xFFFF
    return crc


def _encode_frame(payload: bytes, protocol_type: int = WSV2_PROTOCOL_JSON) -> bytes:
    """Wrap a JSON payload in Studio's V2 framing envelope.

    Layout (10-byte header + payload):

    ::

        bytes 0-1   : 0xBA 0xBE                   (FRAME_HEADER magic)
        bytes 2-4   : payload length, big-endian  (3 bytes)
        byte  5     : protocol_type (low 7 bits); bit 7 = 0 → CRC enabled
        bytes 6-7   : CRC-16/ARC of payload, big-endian
        bytes 8-9   : CRC-16/ARC of bytes 0-7,  big-endian (header CRC)
        bytes 10-…  : payload bytes
    """
    header = bytearray(10)
    header[0:2] = WSV2_FRAME_HEADER
    length = len(payload)
    header[2] = (length >> 16) & 0xFF
    header[3] = (length >> 8) & 0xFF
    header[4] = length & 0xFF
    header[5] = protocol_type & 0x7F  # top bit 0 = CRC enabled
    payload_crc = _crc16(payload)
    header[6] = (payload_crc >> 8) & 0xFF
    header[7] = payload_crc & 0xFF
    header_crc = _crc16(bytes(header[0:8]))
    header[8] = (header_crc >> 8) & 0xFF
    header[9] = header_crc & 0xFF
    return bytes(header) + payload


def _decode_frames(buffer: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """Extract complete frames from ``buffer``.

    Returns ``(frames, remainder)`` where each frame is
    ``(protocol_type, payload_bytes)`` and ``remainder`` is the unread
    tail (used by callers that aggregate fragmented WS messages).
    Frames with a bad header or payload CRC are silently skipped — the
    parser advances one byte and re-syncs on the next ``0xBA 0xBE``.
    """
    frames: list[tuple[int, bytes]] = []
    pos = 0
    n = len(buffer)
    while pos + 10 <= n:
        if buffer[pos] != 0xBA or buffer[pos + 1] != 0xBE:
            pos += 1
            continue
        length = (
            (buffer[pos + 2] << 16)
            | (buffer[pos + 3] << 8)
            | buffer[pos + 4]
        )
        total = 10 + length
        if pos + total > n:
            break
        header = buffer[pos:pos + 8]
        header_crc = (buffer[pos + 8] << 8) | buffer[pos + 9]
        if _crc16(header) != header_crc:
            pos += 1
            continue
        crc_disabled = bool(buffer[pos + 5] & 0x80)
        protocol_type = buffer[pos + 5] & 0x7F
        payload = buffer[pos + 10:pos + total]
        if not crc_disabled:
            payload_crc = (buffer[pos + 6] << 8) | buffer[pos + 7]
            if _crc16(payload) != payload_crc:
                pos += 1
                continue
        frames.append((protocol_type, payload))
        pos += total
    return frames, buffer[pos:]

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
    """SSL context that accepts self-signed device certs.

    Uses ``PROTOCOL_TLS_CLIENT`` rather than
    ``ssl.create_default_context()`` to widen cipher overlap with V2
    firmware's TLS stack. Studio inherits Chromium's permissive
    defaults; Python's ``create_default_context`` is stricter
    (security-level ≥ 2, narrower cipher list) which can fail to
    negotiate against older OpenSSL/BoringSSL revisions running on the
    Allwinner-H3 controller.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def probe_v2(host: str, timeout: float = WSV2_PROBE_TIMEOUT) -> bool:
    """Quick port probe to detect whether a host speaks V2.

    Opens a TLS WebSocket to ``wss://<host>:28900/websocket?…&function=instruction``
    and waits for any frame. Returns True on success, False on any error
    or timeout. No state is held — callers create a real protocol instance
    afterwards.

    The parity handshake (`/v1/user/parity`) is *not* sent here — Studio
    itself does not probe; it learns the device family via the binding
    flow. A reachable TLS WS on port 28900 is enough to disambiguate V2
    from the legacy REST family on port 8080.
    """
    url = (
        f"wss://{host}:{WSV2_PORT}{WSV2_PATH}"
        f"?id={uuid.uuid4()}&function=instruction"
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
    """One in-flight request awaiting its response by ``transactionId``."""

    __slots__ = ("future", "deadline")

    def __init__(self, deadline: float) -> None:
        self.future: asyncio.Future[dict[str, Any]] = asyncio.Future()
        self.deadline = deadline


def _local_timezone() -> str:
    """Best-effort IANA timezone string for the parity handshake."""
    try:
        tz = datetime.now().astimezone().tzinfo
        return str(tz) if tz is not None else ""
    except Exception:
        return ""


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
        self._pending: dict[int, _PendingRequest] = {}
        self._heartbeat_pending: asyncio.Future[dict[str, Any]] | None = None
        self._transaction_counter = 0
        self._latest: dict[str, Any] = {}
        self._connected = False
        self._connect_lock = asyncio.Lock()
        # Aggregator for partial BINARY frames — V2 firmware sometimes
        # splits a single CRC-wrapped envelope across multiple WS
        # messages, so we always feed bytes through ``_decode_frames``
        # which yields whole envelopes only.
        self._rx_buffer = bytearray()

    def _next_transaction_id(self) -> int:
        """Mirror Studio's ``generateTransactionId`` — wrap below the
        reserved ping id so the two pools never collide."""
        self._transaction_counter += 1
        if self._transaction_counter > WSV2_TRANSACTION_ID_WRAP:
            self._transaction_counter = 1
        return self._transaction_counter

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
            f"?id={uuid.uuid4()}&function=instruction"
        )
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        _LOGGER.debug("V2 connecting to %s", url)
        try:
            self._ws_instr = await self._session.ws_connect(
                url,
                ssl=_ssl_context(),
                timeout=aiohttp.ClientTimeout(total=15),
                # RFC 6455 PING/PONG every 20 s — mirrors Chromium's
                # implicit WebSocket keepalive. Surfaces dead sockets
                # faster than waiting for our app-level pong-timeout.
                heartbeat=20.0,
                max_msg_size=0,
                # Origin Studio's renderer sends. Custom scheme
                # ``atomm:`` is registered as privileged + secure +
                # corsEnabled (see Electron main.js
                # ``protocol.registerSchemesAsPrivileged``); the
                # renderer page lives at ``atomm://renderer/...`` so the
                # shared worker's WebSocket handshake reports this Origin.
                headers={"Origin": "atomm://renderer"},
                # ``compress`` left at aiohttp default (15) so we offer
                # ``permessage-deflate; client_max_window_bits=15`` —
                # closest match to Chromium's valueless offer per
                # RFC 7692 §4.
            )
        except Exception as err:
            _LOGGER.debug("V2 connect failed for %s: %s", self.host, err)
            await self._close_quiet()
            raise ConnectionError(f"V2 WebSocket connect failed: {err}") from err
        _LOGGER.debug("V2 WS open to %s — sending parity handshake", self.host)
        self._connected = True
        self._reader_task = asyncio.create_task(self._reader_loop())
        # Parity handshake must complete before any user request fires
        # — V2 firmware closes the WS otherwise.
        try:
            await self._send_first_message()
        except Exception as err:
            _LOGGER.info(
                "V2 parity handshake failed for %s: %s", self.host, err,
            )
            await self._close_quiet()
            raise ConnectionError(
                f"V2 parity handshake failed: {err}"
            ) from err
        _LOGGER.debug("V2 parity handshake OK — starting heartbeat")
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
        if self._heartbeat_pending is not None and not self._heartbeat_pending.done():
            self._heartbeat_pending.set_exception(
                ConnectionError("V2 WebSocket closed")
            )
        self._heartbeat_pending = None
        self._transaction_counter = 0
        self._rx_buffer = bytearray()
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
        _skip_connect: bool = False,
    ) -> dict[str, Any]:
        """Send a JSON request frame, await the matching response.

        Returns the parsed ``data`` field from the response. Raises
        ``ConnectionError`` on disconnect, ``asyncio.TimeoutError`` on
        timeout, ``RuntimeError`` on non-zero ``code``.

        ``_skip_connect`` is used internally by ``_send_first_message``
        which runs as part of the connect flow itself.
        """
        if not _skip_connect and (not self._connected or self._ws_instr is None):
            await self.connect()
        if self._ws_instr is None:
            raise ConnectionError("V2 WebSocket not connected")
        transaction_id = self._next_transaction_id()
        payload: dict[str, Any] = {
            "type": "request",
            "method": method.upper(),
            "url": url,
            "params": params if params is not None else {},
            "data": data if data is not None else {},
            "timestamp": int(time.time() * 1000),
            "transactionId": transaction_id,
        }
        deadline = time.monotonic() + timeout
        pending = _PendingRequest(deadline)
        self._pending[transaction_id] = pending
        frame = _encode_frame(_json_compact(payload).encode("utf-8"))
        _LOGGER.debug(
            "V2 TX %s %s txn=%d frame_len=%d",
            method, url, transaction_id, len(frame),
        )
        try:
            await self._ws_instr.send_bytes(frame)
        except Exception as err:
            self._pending.pop(transaction_id, None)
            raise ConnectionError(f"V2 send failed: {err}") from err
        try:
            response = await asyncio.wait_for(pending.future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(transaction_id, None)
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

    async def _send_first_message(self) -> None:
        """Studio's ``sendFirstMessageCode`` — must succeed before any
        other request fires, otherwise V2 firmware tears down the WS.
        """
        await self.request(
            "/v1/user/parity",
            "GET",
            data={
                "userID": WSV2_USER_UUID,
                "userKey": WSV2_FIRST_MESSAGE_USER_KEY,
                "timezone": _local_timezone(),
            },
            timeout=WSV2_FIRST_MESSAGE_TIMEOUT,
            _skip_connect=True,
        )

    # ── reader / heartbeat loops ──────────────────────────────────────

    async def _reader_loop(self) -> None:
        ws = self._ws_instr
        if ws is None:
            return
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    self._handle_binary(msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    _LOGGER.debug(
                        "V2 RX TEXT (unexpected on V2): %r",
                        msg.data[:200] if isinstance(msg.data, str) else msg.data,
                    )
                    # V2 firmware doesn't normally send TEXT frames, but
                    # tolerate them in case a debug/firmware build does.
                    self._handle_text(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    _LOGGER.info(
                        "V2 WS closed by peer (msg.type=%s, close_code=%s, reason=%r)",
                        msg.type,
                        getattr(ws, "close_code", None),
                        getattr(ws, "_close_message", None),
                    )
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("V2 reader loop exited: %s", err)
        finally:
            self._connected = False

    async def _heartbeat_loop(self) -> None:
        """Studio HEART_MESSAGE ping every ``WSV2_HEARTBEAT_SECONDS``.

        Studio's worker fires `/v1/user/ping` with the fixed
        transactionId 65510 and starts a pong-timeout watchdog
        (`heartbeatTimeout`). If no response arrives within the window
        the connection is closed and a reconnect is scheduled. We
        mirror that behaviour: a missed pong tears down the WS so the
        coordinator's ``_async_update_data`` reconnects on the next
        poll.
        """
        try:
            while self._connected:
                await asyncio.sleep(WSV2_HEARTBEAT_SECONDS)
                if self._ws_instr is None or self._ws_instr.closed:
                    return
                # Field order matches Studio's HEART_MESSAGE template
                # spread by ``{...HEART_MESSAGE, timestamp: Date.now()}``:
                # type, method, url, transactionId, data, params, timestamp.
                payload = {
                    "type": "request",
                    "method": "GET",
                    "url": "/v1/user/ping",
                    "transactionId": WSV2_PING_TRANSACTION_ID,
                    "data": {},
                    "params": {},
                    "timestamp": int(time.time() * 1000),
                }
                self._heartbeat_pending = asyncio.Future()
                frame = _encode_frame(_json_compact(payload).encode("utf-8"))
                try:
                    await self._ws_instr.send_bytes(frame)
                except Exception as err:
                    _LOGGER.debug("V2 heartbeat send failed: %s", err)
                    self._heartbeat_pending = None
                    asyncio.create_task(self._close_quiet())
                    return
                try:
                    await asyncio.wait_for(
                        self._heartbeat_pending,
                        timeout=WSV2_HEARTBEAT_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    _LOGGER.info(
                        "V2 heartbeat timeout (%.1fs) — closing %s",
                        WSV2_HEARTBEAT_TIMEOUT, self.host,
                    )
                    asyncio.create_task(self._close_quiet())
                    return
                except ConnectionError:
                    return
                finally:
                    self._heartbeat_pending = None
        except asyncio.CancelledError:
            raise

    def _dispatch_event(self, event: dict[str, Any]) -> None:
        # Responses carry ``type:"response"`` and a numeric
        # ``transactionId`` either at top-level or nested under
        # ``data.transactionId`` (Studio's dispatcher checks both).
        if event.get("type") == "response":
            # Studio's matcher accepts any JS number for ``transactionId``
            # — Python's ``json`` returns int for whole numbers, but we
            # tolerate float / numeric-string variants defensively in
            # case some firmware revision encodes them differently.
            def _coerce_txn(value: Any) -> int | None:
                if isinstance(value, bool):
                    return None
                if isinstance(value, int):
                    return value
                if isinstance(value, float) and value.is_integer():
                    return int(value)
                if isinstance(value, str) and value.isdigit():
                    return int(value)
                return None

            txn = _coerce_txn(event.get("transactionId"))
            if txn is None:
                inner = event.get("data")
                if isinstance(inner, dict):
                    txn = _coerce_txn(inner.get("transactionId"))
            if txn is not None:
                if txn == WSV2_PING_TRANSACTION_ID:
                    if (
                        self._heartbeat_pending is not None
                        and not self._heartbeat_pending.done()
                    ):
                        self._heartbeat_pending.set_result(event)
                    return
                pending = self._pending.pop(txn, None)
                if pending is not None and not pending.future.done():
                    pending.future.set_result(event)
                return
        # Otherwise treat as a push event.
        self._dispatch_push(event)

    def _handle_text(self, raw: str) -> None:
        """TEXT-frame fallback (rare on V2 firmware)."""
        try:
            event = json.loads(raw)
        except Exception:
            return
        if isinstance(event, dict):
            self._dispatch_event(event)

    def _handle_binary(self, raw: bytes) -> None:
        """Decode CRC-wrapped V2 frames; aggregate across WS messages."""
        _LOGGER.debug(
            "V2 RX binary chunk len=%d head=%s",
            len(raw), raw[:16].hex(),
        )
        self._rx_buffer.extend(raw)
        frames, remainder = _decode_frames(bytes(self._rx_buffer))
        if not frames and len(self._rx_buffer) > 0:
            _LOGGER.debug(
                "V2 RX no complete frame yet, buffer=%d bytes",
                len(self._rx_buffer),
            )
        self._rx_buffer = bytearray(remainder)
        for protocol_type, payload in frames:
            if protocol_type != WSV2_PROTOCOL_JSON:
                _LOGGER.debug(
                    "V2 ignored frame protocol_type=%d len=%d",
                    protocol_type, len(payload),
                )
                continue
            try:
                event = json.loads(payload.decode("utf-8"))
            except Exception as err:
                _LOGGER.debug(
                    "V2 RX JSON parse failed: %s payload=%r",
                    err, payload[:200],
                )
                continue
            _LOGGER.debug(
                "V2 RX event type=%s url=%s txn=%s",
                event.get("type"), event.get("url"),
                event.get("transactionId"),
            )
            if isinstance(event, dict):
                self._dispatch_event(event)

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
            f"?id={uuid.uuid4()}&function=file_stream"
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
