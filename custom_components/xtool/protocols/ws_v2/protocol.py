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
import hashlib
import json
import logging
import ssl
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiohttp
from homeassistant.util.ssl import get_default_no_verify_context

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
# Studio's ``bl.FILE_TRANSFER`` frame code — the file_stream WS
# carries the sliding-window file-transfer packets under this
# protocol type. Payload is a binary FILE_REQUEST / FILE_DATA
# packet, not a JSON envelope.
WSV2_PROTOCOL_FILE_TRANSFER = 33

# --- Studio file-transfer constants (verified against Studio v1.7.23
# ``main.e_TJj9fA.js`` — ``Iv/Lv/Rv/zv/Bv/Vv/Wv`` blocks). ------------
# Binary packet opcodes carried in the FILE_TRANSFER payload.
WSV2_FILE_OP_REQUEST = 1     # ``zv.FILE_REQUEST`` — client → device
WSV2_FILE_OP_DATA = 129      # ``zv.FILE_DATA`` — device → client
# Fixed packet-header sizes.
WSV2_FILE_REQUEST_HEADER = 10  # ``Bv.FILE_REQUEST_HEADER``
WSV2_FILE_DATA_HEADER = 7      # ``Bv.FILE_DATA_HEADER``
# Digest type for the initial handshake — MD5 = 1 matches Studio's
# ``Vv.MD5``. Firmware only accepts this value on M2 firmware
# ``40.141.010.01.ht03``; SHA variants exist in Studio's enum but
# are not documented as functional.
WSV2_FILE_DIGEST_MD5 = 1
# Default window and packet sizes (Studio's ``Iv`` = 5 MiB window,
# ``Lv`` = 1 MiB packet). The firmware may echo a smaller
# ``packetsize`` in the handshake reply — respect whichever is
# smaller.
WSV2_FILE_DEFAULT_WINDOW = 5 * 1024 * 1024
WSV2_FILE_DEFAULT_PACKET = 1 * 1024 * 1024
# Channel IDs are single-byte (Studio ``Rv.MAX_CHANNEL``). The
# integration reuses a monotonic counter modulo 256; simultaneous
# downloads pick distinct channel slots.
WSV2_FILE_MAX_CHANNEL = 255
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


def _encode_frame(
    payload: bytes,
    protocol_type: int = WSV2_PROTOCOL_JSON,
    cal_crc16: bool = True,
) -> bytes:
    """Wrap a JSON payload in Studio's V2 framing envelope.

    Layout (10-byte header + payload):

    ::

        bytes 0-1   : 0xBA 0xBE                   (FRAME_HEADER magic)
        bytes 2-4   : payload length, big-endian  (3 bytes)
        byte  5     : protocol_type (low 7 bits); bit 7 = 0 → CRC on,
                                                   bit 7 = 1 → CRC off
        bytes 6-7   : CRC-16/ARC of payload, big-endian
                       (zeroed when ``cal_crc16 = False``)
        bytes 8-9   : CRC-16/ARC of bytes 0-7,  big-endian (header CRC)
        bytes 10-…  : payload bytes

    Studio's ``FileDownloader.requestNextDataPacket`` sends
    FILE_REQUEST packets with ``sendCmd(targetId, packet, false)``
    — the third arg is ``calCrc16``. FILE_REQUEST packets go out
    with bit 7 of the protocol_type byte set and a zeroed payload
    CRC. Mirroring that avoids the M2 firmware silently ignoring
    our packets on the file_stream WS.
    """
    header = bytearray(10)
    header[0:2] = WSV2_FRAME_HEADER
    length = len(payload)
    header[2] = (length >> 16) & 0xFF
    header[3] = (length >> 8) & 0xFF
    header[4] = length & 0xFF
    header[5] = (protocol_type & 0x7F) | (0 if cal_crc16 else 0x80)
    payload_crc = _crc16(payload) if cal_crc16 else 0
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
    "P_EMERGENCY_STOP":     XtoolStatus.ERROR_LIMIT,
}


# Map firmware-emitted button-press strings to the canonical
# ``XtoolEvent`` button event types. The "SHOERT_PRESS" misspelling
# was reported in live MetalFab traces (issue #3) — normalising here
# keeps the entity vocabulary stable regardless of typo or case.
_BUTTON_EVENT_TYPE_MAP: dict[str, str] = {
    "SHORT_PRESS":  "short_press",
    "SHOERT_PRESS": "short_press",  # firmware typo
    "LONG_PRESS":   "long_press",
    "DOUBLE_PRESS": "double_press",
    "DOUBLE_CLICK": "double_press",
}


def _normalise_button_event(raw: object) -> str | None:
    """Normalise ``/button/status BUTTON`` push ``type`` strings."""
    if raw is None:
        return None
    return _BUTTON_EVENT_TYPE_MAP.get(str(raw).upper())


# ``/accessory/status`` push parsers, keyed by the leading M-code
# head. The push payload mirrors the per-accessory ``info_mcode``
# poll reply (minus the M-code prefix), so each parser delegates
# to the existing ``parse_info`` in the matching accessory
# definition. Returned dict drops into the accessory's ``fields``
# in the V2 coordinator's accessory-merge step.
def _parse_accessory_push(head: str, body: str) -> dict[str, Any]:
    """Decode an `/accessory/status` push body for the given M-code.

    ``head`` is the leading M-code (e.g. ``M9064``); ``body`` is
    the trailing token string (everything after the first space in
    the push's ``mcode`` field).
    """
    # Lazy import — accessories package depends on this module's
    # F0F7 helpers, so we can't pull definitions at module load.
    from ..accessories import ACCESSORY_DEFINITIONS
    from ..accessories.base import (
        MCODE_FAN_INFO,
        MCODE_FAN_SET_GEAR,
        MCODE_PURIFIER_INFO,
        MCODE_PURIFIER_SET_GEAR,
    )

    # The push frames carry the *output* of an action, not the
    # info-poll reply, so the wire shape differs per M-code.
    # ``M9064 A<n> B<n> C<n> D<n> S<n>`` is the DuctFanV3 push
    # shape (5 positional tokens, no version anchor). DuctFan V1
    # entities don't read the V3-only fields ``mode_class`` /
    # ``current_gear``, so applying the V3 parser to a V1 push
    # is harmless; if a future V1-IF2 capture shows divergent
    # wire shape we can re-introduce a per-type dispatch here.
    if head == MCODE_FAN_SET_GEAR:
        from ..accessories.duct_fan import parse_fan_v3_push
        try:
            return parse_fan_v3_push(body)
        except Exception:
            return {}
    if head == MCODE_FAN_INFO:
        try:
            return ACCESSORY_DEFINITIONS["DuctFanV3"].parse_info(body)
        except Exception:
            return {}
    if head in (MCODE_PURIFIER_INFO, MCODE_PURIFIER_SET_GEAR):
        try:
            return ACCESSORY_DEFINITIONS["Purifier"].parse_info(body)
        except Exception:
            return {}
    return {}


_LOGGER = logging.getLogger(__name__)


def _ssl_context() -> ssl.SSLContext:
    """SSL context that accepts self-signed device certs.

    Uses HA's ``get_default_no_verify_context()`` helper which is
    cached at module import time and returns an SSLContext with
    ``check_hostname=False`` + ``verify_mode=CERT_NONE`` already set.
    Avoids the three blocking calls
    (``ssl.create_default_context``, ``set_default_verify_paths``,
    ``load_default_certs``) that ``ssl.create_default_context()``
    would otherwise trigger on the event loop.
    """
    return get_default_no_verify_context()


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

    # Push-cached fields that overrule the poll snapshot when a push
    # has more recent data. Used by both ``poll_state`` (drain at end
    # of cycle) and the push-notify callback (drain + emit immediately
    # so entity state surfaces in HA without waiting for next poll).
    _LATEST_STATE_FIELDS: tuple[str, ...] = (
        "status", "task_id", "task_time", "cover_open", "machine_lock",
        "last_job_time_seconds",
        "cooling_fan_running", "smoking_fan_running", "uv_fire_alarm",
        "water_pump_running", "water_line_ok", "water_temperature",
        "water_flow", "drawer_open", "cpu_fan_running",
        "gyro_x", "gyro_y", "gyro_z",
        "position_x", "position_y", "position_z",
        "last_distance_mm", "display_brightness",
        "fill_light_a", "fill_light_b",
        "flame_alarm_v2_enabled", "beep_enabled_v2",
        "gap_check_enabled", "machine_lock_check_enabled",
        "purifier_timeout", "working_mode",
        "stops_when_moved", "auto_sleep_enable",
        "air_assist_close_delay", "smoking_fan_duration",
        "air_assist_gear_cut", "air_assist_gear_grave",
        "sleep_timeout", "sleep_timeout_open_gap",
        "fill_light_auto_off", "ir_light_auto_off",
        "print_tool_type", "flame_level_hl",
        "working_seconds", "session_count", "standby_seconds",
        "tool_runtime_seconds", "alarm_present",
    )

    # URL routing keys carried in the V2 instruction-frame ``url:`` field.
    # Exposed as class attributes so per-model subclasses (P2S, DT001, M2…)
    # can override them without copying the whole ``poll_state`` body. The
    # values below are the F1/F2-family norm — verified against Studio
    # v1.7.23 per-model extension bundles in ``exts.zip``.
    PATH_DEVICE_INFO = "/v1/device/machineInfo"
    PATH_RUNTIME_INFOS = "/v1/device/runtime-infos"
    PATH_CONFIGS_GET = "/v1/device/configs"
    PATH_CONFIGS_SET = "/v1/device/configs"
    PATH_STATISTICS = "/v1/device/statistics"
    PATH_PROGRESS = "/v1/processing/progress"
    PATH_ALARMS = "/v1/device/alarms"
    PATH_PROCESSING_STATE = "/v1/processing/state"
    PATH_CAMERA_SNAP = "/v1/camera/snap"
    PATH_UPGRADE_MODE = "/v1/device/upgrade-mode"

    def _apply_latest_to_state(self, state: XtoolDeviceState) -> None:
        """Drain push-cached ``self._latest`` values into ``state``.

        Called from both ``poll_state`` (end-of-cycle merge) and the
        push-notify callback wired by the coordinator (immediate
        propagation when a DEVICE_CONFIG / peripheral push arrives).
        """
        for k in self._LATEST_STATE_FIELDS:
            if k in self._latest:
                setattr(state, k, self._latest[k])

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
        # Single-byte channel counter for the file-transfer sliding-window
        # protocol (Studio ``Rv.MAX_CHANNEL = 255``). Each concurrent
        # download picks a distinct channel; the wraparound is fine —
        # channels only clash if 256+ downloads race simultaneously.
        self._file_channel_counter = 0
        self._latest: dict[str, Any] = {}
        self._connected = False
        self._connect_lock = asyncio.Lock()
        # Coordinator stamps the resolved XtoolDeviceModel via set_model();
        # poll_state reads it to gate per-model peripheral queries.
        self._model: Any = None
        # Slow-cadence counter for /v1/device/statistics + /v1/device/configs
        # — re-querying every poll wastes bandwidth + device CPU.
        self._poll_counter = 0
        # Per-connection cache of peripheral types the firmware refused
        # (`code -3`, `code -2`, `code 10`, `code 1`). Avoids re-hammering
        # endpoints the model doesn't expose. Cleared on every fresh
        # connect so a firmware upgrade re-probes.
        self._unsupported_peripheral_types: set[str] = set()
        # Per-connection cache of /v1/device/configs keys whose firmware
        # validator demands JSON ``true``/``false`` instead of int 0/1.
        # HJ003 / GS003 firmware rejects bool with ``code 1: failed`` so
        # set_config defaults to int (issue #3, v2.3.3); F2 Ultra UV
        # firmware (40.130.021.00.ht2) rejects the int with
        # ``[type_error.302] type must be boolean, but is number`` —
        # set_config detects that error string, caches the key here and
        # retries with the bool. Cleared on reconnect so a firmware
        # upgrade re-probes.
        self._config_keys_bool: set[str] = set()
        # Per-connection cache of top-level endpoints the firmware
        # rejected (``code -2 / -3 / 10 / 1 / 404``). Mirrors the
        # ``_unsupported_peripheral_types`` pattern but for the slow-
        # cadence poll endpoints (`/v1/device/alarms`,
        # `/v1/device/statistics`) that several V2 models don't
        # expose — F1 / GS005 / HJ003 / M1Ultra / P3 / P2S / DT001 all
        # lack `/v1/device/alarms`; GS006 / P2S / DT001 lack
        # `/v1/device/statistics`. Cached endpoints get skipped on
        # subsequent polls to suppress log noise.
        self._unsupported_endpoints: set[str] = set()
        # Push-event queue drained by the coordinator each poll. Each
        # entry is ``(kind, event_type, attrs)``. Push handlers in
        # ``_dispatch_push`` append; the WS-V2 coordinator forwards
        # everything to HA's dispatcher under the event-loop thread.
        self._pending_events: list[tuple[str, str, dict[str, Any] | None]] = []
        # Track the most recently push-emitted job event so the
        # coordinator's poll-cycle transition detector doesn't
        # re-fire the same event when the next poll arrives. Tuple
        # is ``(task_id_or_empty, event_kind)``.
        self._last_push_job_event: tuple[str, str] | None = None
        # `/accessory/status` push lands here as
        # ``(mcode_head, {field: value, …})`` tuples — the WS-V2
        # coordinator drains the list in ``_poll_accessories`` and
        # merges parsed values into the matching paired-accessory
        # state so the entity layer refreshes between BT polls.
        self._pending_accessory_updates: list[tuple[str, dict[str, Any]]] = []
        # Coordinator sets this callback during connect; it fires after
        # every ``_dispatch_push`` so entity state surfaces in HA
        # without waiting for the next 5s poll cycle. ``None`` until
        # the coordinator wires it (or for code paths that don't need
        # immediate UI feedback).
        self._push_notify: Callable[[], None] | None = None
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
        # Reset the per-connection peripheral-rejection cache — fresh
        # firmware revisions might expose types the previous one didn't.
        self._unsupported_peripheral_types.clear()
        self._unsupported_endpoints.clear()
        self._config_keys_bool.clear()
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
            "V2 TX %s %s txn=%d params=%s data=%s",
            method, url, transaction_id, params, data,
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
            _LOGGER.debug(
                "V2 RX %s %s txn=%d code=%d msg=%s",
                method, url, transaction_id, code, msg,
            )
            raise RuntimeError(
                f"V2 {method} {url} returned code {code}: {msg}"
            )
        result = (
            response.get("data") if isinstance(response, dict) else {}
        ) or {}
        _LOGGER.debug(
            "V2 RX %s %s txn=%d data=%r",
            method, url, transaction_id, result,
        )
        return result

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

    # ── action helpers (entity write-paths) ──────────────────────────

    async def set_config(self, key: str, value: Any) -> dict[str, Any]:
        """PUT a single key into ``/v1/device/configs``.

        Studio's bundle uses ``{alias:"config", type:"user",
        kv:{<key>:<value>}}`` for runtime configuration; the same
        envelope is consumed by the V2 firmware's ``configs`` handler.

        Bool / int divergence: V2 firmware schemas split per family:

        * HJ003 (MetalFab) + GS003 (F1 Ultra V2) reject JSON
          ``true``/``false`` for boolean-ish keys with ``code 1:
          failed`` — issue #3 (v2.3.3) coerced bool → int 0/1 to fix.
        * F2 Ultra UV firmware ``40.130.021.00.ht2`` typed those keys
          strictly as boolean and rejects the int with
          ``[type_error.302] type must be boolean, but is number``.

        Strategy: default to int 0/1 (preserves the v2.3.3 fix). On the
        F2UV-specific type-error response, cache the key in
        ``_config_keys_bool`` and retry with the bool. Subsequent
        writes to a cached key skip the int attempt.
        """
        coerced: Any = value
        if isinstance(value, bool) and key not in self._config_keys_bool:
            coerced = 1 if value else 0
        elif (
            isinstance(value, int)
            and not isinstance(value, bool)
            and key in self._config_keys_bool
        ):
            # Key cached as bool on this connection — coerce int back to bool.
            coerced = bool(value)
        try:
            return await self.request(
                self.PATH_CONFIGS_SET,
                "PUT",
                data={"alias": "config", "type": "user",
                      "kv": {key: coerced}},
            )
        except RuntimeError as err:
            err_str = str(err)
            # F2UV firmware ``40.130.021.00.ht2`` raises this error string
            # when an int is passed for a key the firmware schema types as
            # bool. Retry once as bool, regardless of whether the caller
            # passed a bool or an int.
            type_error = "type must be boolean" in err_str
            if (
                type_error
                and key not in self._config_keys_bool
                and isinstance(value, (bool, int))
            ):
                _LOGGER.debug(
                    "V2 set_config key=%s typed boolean by firmware "
                    "— retrying with bool, caching for this connection",
                    key,
                )
                self._config_keys_bool.add(key)
                return await self.request(
                    self.PATH_CONFIGS_SET,
                    "PUT",
                    data={"alias": "config", "type": "user",
                          "kv": {key: bool(value)}},
                )
            raise

    async def set_peripheral(
        self,
        peripheral_type: str,
        action: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """PUT a peripheral-control command.

        Studio's V2 bundles route every peripheral write through
        ``PUT /v1/peripheral/param?type=<peripheral>`` with body
        ``{action: "<verb>", ...extra}`` — the same envelope used by
        the GET state-query path. There is no ``/v1/peripheral/control``
        route in any V2 firmware (F1 / GS003-009 / HJ003 / M1Ultra /
        F2UV bundles all confirm), and F2 Ultra UV firmware
        ``40.130.021.00.ht2`` returns ``code 404: Not Found`` for
        anything addressed there.

        Action verb is peripheral-specific (``"on"``/``"off"`` for
        binary peripherals, ``"set_brightness"`` / ``"home"`` /
        ``"measure"`` for richer ones).
        """
        body: dict[str, Any] = {}
        if action is not None:
            body["action"] = action
        if extra:
            body.update(extra)
        return await self.request(
            "/v1/peripheral/param",
            "PUT",
            params={"type": peripheral_type},
            data=body,
        )

    async def set_processing_state(self, action: str) -> dict[str, Any]:
        """PUT ``/v1/processing/state?action=<pause|start|stop>``.

        Drives the job-control buttons (pause / resume / cancel). All
        WS-V2 Studio bundles expose this route with the same shape:
        ``pausePrint`` → ``action=pause``, resume = ``start``,
        cancel = ``stop``. The legacy ``set_mode("P_PAUSE")`` path used
        ``/v1/device/mode`` which is reserved for IDLE / AUTOFOCUS /
        MEASURE transitions and rejected the job verbs with
        ``code 1: failed`` on F2 Ultra UV.
        """
        return await self.request(
            self.PATH_PROCESSING_STATE,
            "PUT",
            params={"action": action},
            data={},
        )

    async def set_mode(self, mode: str) -> dict[str, Any]:
        """PUT to ``/v1/device/mode`` to transition the device mode.

        Used for ``P_IDLE`` / ``P_AUTOFOCUS`` / ``P_MEASURE`` device
        states. Job control (pause / resume / cancel) lives on
        ``/v1/processing/state`` instead — see
        :meth:`set_processing_state`.
        """
        return await self.request(
            "/v1/device/mode",
            "PUT",
            data={"mode": mode},
        )

    async def parts_control(
        self, mcode: str, prefix: bytes, timeout: float = 6.0,
    ) -> str | None:
        """Tunnel an M-code to a BT accessory through ``/v1/parts/control``.

        Body shape: ``{link:"uart485", data_b64:<F0F7-encoded>}``.
        Response carries the F0F7-framed reply under the same
        ``data_b64`` field. Returns the inner payload (stripped of
        the leading M-code token) or ``None`` on failure.
        """
        from ..accessories import encode_f0f7, decode_f0f7

        encoded = encode_f0f7(mcode, prefix)
        _LOGGER.debug(
            "V2 parts_control TX mcode=%r prefix=%r b64=%s",
            mcode, prefix, encoded,
        )
        try:
            result = await self.request(
                "/v1/parts/control",
                "POST",
                data={"link": "uart485", "data_b64": encoded},
                timeout=timeout,
            )
        except Exception as err:
            _LOGGER.debug("V2 parts_control %s failed: %s", mcode, err)
            return None
        if not isinstance(result, dict):
            _LOGGER.debug(
                "V2 parts_control %r: non-dict reply %r", mcode, result,
            )
            return None
        reply_b64 = result.get("data_b64")
        if not isinstance(reply_b64, str) or not reply_b64:
            _LOGGER.debug(
                "V2 parts_control %r: no data_b64 in reply %r",
                mcode, result,
            )
            return None
        decoded = decode_f0f7(reply_b64, mcode)
        _LOGGER.debug(
            "V2 parts_control RX mcode=%r decoded=%r", mcode, decoded,
        )
        return decoded

    async def camera_snap(self, camera_name: str = "main") -> bytes | None:
        """Capture a JPEG snapshot from one of the device's cameras.

        V2 camera capture is a two-step flow per the Studio bundle's
        ``captureGlobalImage`` definition:

        1. ``GET /v1/camera/snap?name=<camera-name>`` on the
           instruction channel returns ``{filename:"<path>"}``.
           Firmware-canonical names: the F2 family
           (GS004/006/007/009) + HJ003 (MetalFab) expose ``main``
           + ``deep``; F1 Ultra V2 (GS003) is single ``main``; the
           P-family carries the legacy ``overview`` / ``closeup``
           names through.
        2. :meth:`_download_file_stream` runs Studio's binary
           sliding-window file-transfer protocol against
           ``PUT /v1/filetransfer/download`` + the ``function=
           file_stream`` WS + ``PUT /v1/filetransfer/finish``.
        """
        try:
            snap = await self.request(
                self.PATH_CAMERA_SNAP,
                "GET",
                params={"name": camera_name},
                timeout=15.0,
            )
        except Exception as err:
            _LOGGER.debug("V2 camera_snap %s failed: %s", camera_name, err)
            return None
        if not isinstance(snap, dict):
            return None
        filename = snap.get("filename")
        if not isinstance(filename, str) or not filename:
            _LOGGER.debug(
                "V2 camera_snap %s: no filename in response %s",
                camera_name, snap,
            )
            return None

        try:
            blob = await self._download_file_stream(filename, file_type=5)
        except Exception as err:
            _LOGGER.debug(
                "V2 camera_snap %s: file_stream download failed: %s",
                camera_name, err,
            )
            return None

        return blob if blob else None

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
        # Signal the coordinator that ``_latest`` may carry fresh
        # state — let it drain into ``coordinator.data`` and re-render
        # entities immediately, instead of waiting up to one full
        # poll cycle (5 s) for the next ``poll_state`` drain.
        notify = self._push_notify
        if notify is not None:
            try:
                notify()
            except Exception as err:
                _LOGGER.debug("V2 push_notify callback failed: %s", err)

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

        _LOGGER.debug(
            "V2 push url=%s module=%s type=%s info=%r",
            url, module, typ, info,
        )

        # BT-accessory live status — wire shape not yet decoded; logged
        # explicitly so a future debug-log capture can drive the
        # connect/disconnect handler. Keep dispatching normally so any
        # downstream processing can run once we add it.
        if url == "/accessory/status":
            _LOGGER.debug(
                "V2 /accessory/status push (RAW): event=%r",
                event,
            )

        if url == "/work/mode" and module == "STATUS_CONTROLLER" and typ == "MODE_CHANGE":
            mode = ""
            if isinstance(info, dict):
                mode = str(info.get("mode") or "").upper()
                if info.get("taskId") is not None:
                    self._latest["task_id"] = info["taskId"]
                # Only overwrite working_mode when the push carries a
                # non-empty subMode — empty strings (idle / paused
                # transitions) would otherwise blank the entity.
                sub = info.get("subMode")
                if sub:
                    self._latest["working_mode"] = str(sub)
            mapped = WSV2_MODE_MAP.get(mode)
            if mapped is not None:
                self._latest["status"] = mapped

        elif url == "/device/status" and module == "STATUS_CONTROLLER":
            info_str = str(info).lower() if info is not None else ""
            task_id = str(self._latest.get("task_id") or "")
            if typ == "WORK_PREPARED":
                self._latest["status"] = (
                    XtoolStatus.FRAMING if info_str == "framing"
                    else XtoolStatus.PROCESSING_READY
                )
            elif typ == "WORK_STARTED":
                is_framing = info_str == "framing"
                self._latest["status"] = (
                    XtoolStatus.FRAMING if is_framing
                    else XtoolStatus.PROCESSING
                )
                # Emit the job lifecycle event directly here rather
                # than waiting for the coordinator's poll-cycle
                # transition detector — fast jobs (a few seconds)
                # complete entirely between two poll cycles and the
                # coordinator's snapshot misses the IDLE→PROCESSING
                # edge, so the Job event entity would stay Unknown.
                if not is_framing:
                    self._maybe_emit_job_event(
                        "started", task_id,
                        {"task_id": task_id} if task_id else None,
                    )
                else:
                    self._maybe_emit_job_event(
                        "framing_started", task_id, None,
                    )
            elif typ == "WORK_FINISHED":
                is_framing = info_str == "framing"
                self._latest["status"] = (
                    XtoolStatus.IDLE if is_framing
                    else XtoolStatus.FINISHED
                )
                if is_framing:
                    self._maybe_emit_job_event(
                        "framing_finished", task_id, None,
                    )

        elif url == "/work/result" and module == "WORK_RESULT" and typ == "WORK_FINISHED":
            self._latest["status"] = XtoolStatus.FINISHED
            task_id = str(self._latest.get("task_id") or "")
            duration: int | None = None
            if isinstance(info, dict):
                if info.get("timeUse") is not None:
                    try:
                        # ``timeUse`` arrives in **seconds**, not
                        # milliseconds — verified against an F2UV
                        # 1h57m job that ran 12:59:45 → 14:57:28
                        # and reported ``timeUse: 7028`` ≈ 7063 s
                        # wall-clock.
                        secs = int(info["timeUse"])
                        self._latest["task_time"] = secs
                        self._latest["last_job_time_seconds"] = secs
                        duration = secs
                    except (TypeError, ValueError):
                        pass
                if info.get("taskId") is not None:
                    self._latest["task_id"] = info["taskId"]
                    task_id = str(info["taskId"])
            self._maybe_emit_job_event(
                "finished", task_id,
                {"task_id": task_id, "duration": duration},
            )

        elif url == "/gap/status" and module == "GAP":
            if typ == "OPEN":
                self._latest["cover_open"] = True
            elif typ == "CLOSE":
                self._latest["cover_open"] = False

        elif url == "/drawer/status" and module == "DRAWER":
            if typ == "OPEN":
                self._latest["drawer_open"] = True
            elif typ == "CLOSE":
                self._latest["drawer_open"] = False

        elif url == "/machine_lock/status" and module == "MACHINE_LOCK":
            # Device emits OPEN when unlocked, CLOSE when locked.
            if typ == "OPEN":
                self._latest["machine_lock"] = False
            elif typ == "CLOSE":
                self._latest["machine_lock"] = True

        elif (
            url in ("/emergency/status", "/emergency_stop/status")
            and module == "EMERGENCY_STOP"
        ):
            # Two URL spellings exist in the wild: HJ003 (MetalFab)
            # emits ``/emergency/status``; the F-series (GS002/003/
            # 005/006/007/009) firmware emits ``/emergency_stop/
            # status``. Payload is identical — ``VOLTAGE_TRIGGER``
            # when the e-stop button is pressed, ``RESUME`` when
            # released.
            if typ in ("VOLTAGE_TRIGGER", "TRIGGER"):
                self._latest["status"] = XtoolStatus.ERROR_LIMIT
                self._latest["alarm_present"] = True
                self._pending_events.append(
                    ("error", "emergency_stop",
                     {"raw_type": str(typ), "url": url}),
                )
            elif typ == "RESUME":
                self._latest["alarm_present"] = False

        elif url == "/fire/alarm" and module == "FIRE_RECOGNITION":
            # Vision-based flame detection — separate channel from
            # the polled ``state.alarm_present`` (which only catches
            # the IR sensor) and from ``/emergency_stop/status``.
            # Trigger the dedicated fire-warning event entity.
            self._latest["alarm_present"] = True
            self._pending_events.append(
                ("fire_warning", "triggered",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/temperature/alarm":
            self._pending_events.append(
                ("error", "temperature",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/gyro/alarm":
            self._pending_events.append(
                ("error", "gyro",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/laser_head/alarm":
            # Distinct from the ``/laserhead/status BUSY/IDLE`` push
            # (which is just a state flag) — this is a fault.
            self._pending_events.append(
                ("error", "laser_head_fault",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/z_axis/alarm":
            self._pending_events.append(
                ("error", "z_axis_fault",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/u_axis/alarm":
            self._pending_events.append(
                ("error", "u_axis_fault",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/conveyor/alarm":
            self._pending_events.append(
                ("error", "conveyor_fault",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/boards/alarm":
            # Aggregate board-side fault — distinct from
            # ``/board/link CONNECT`` (the accessory-attached push).
            self._pending_events.append(
                ("error", "board_fault",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/camera/alarm":
            self._pending_events.append(
                ("error", "camera_fault",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/bluetooth_dongle/alarm":
            self._pending_events.append(
                ("error", "dongle_fault",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/udisk/alarm":
            self._pending_events.append(
                ("error", "udisk_fault",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/machine_lock_for_md/alarm":
            self._pending_events.append(
                ("error", "machine_lock_md_fault",
                 {"raw_type": str(typ), "info": info}),
            )

        elif url == "/device/info" and module == "MACHINE_INFO" and typ == "INFO":
            # MetalFab returns an empty body from GET
            # /v1/device/machineInfo and only emits the real device
            # info via this push (after the WS opens). Latch the
            # fields the integration's DeviceInfo cares about so the
            # device-card title + firmware-version show up.
            if isinstance(info, dict):
                if info.get("deviceName"):
                    self._latest["device_name"] = info["deviceName"]
                if info.get("sn"):
                    self._latest["serial_number"] = info["sn"]
                if info.get("mac"):
                    self._latest["mac_address"] = info["mac"]
                fw = info.get("firmware") or {}
                if isinstance(fw, dict):
                    pkg = (
                        fw.get("package_version")
                        or fw.get("master_rk3568_mainservice")
                        or fw.get("master_h3_laserservice")
                    )
                    if pkg:
                        self._latest["firmware_version"] = pkg

        elif url == "/accessory/status" and module == "DEVID_MCODE":
            # Stream of M-code-payload updates from paired BT
            # accessories. ``info`` carries ``{devId, mcode}`` —
            # ``mcode`` is the same shape Studio's ``triggerReport``
            # / per-accessory info-poll handlers return, e.g.
            # ``"M9064 A1 B3 C4 D0 S0"`` for a DuctFanV3 gear push.
            # We hand off to a per-M-code parser and queue the
            # resulting fields for the coordinator's accessory
            # state-merge step.
            if isinstance(info, dict):
                mcode = info.get("mcode") or ""
                if isinstance(mcode, str) and mcode.strip():
                    head, _, body = mcode.strip().partition(" ")
                    parsed = _parse_accessory_push(head, body)
                    if parsed:
                        self._pending_accessory_updates.append((head, parsed))
                    else:
                        _LOGGER.debug(
                            "V2 /accessory/status: no parser for %s — "
                            "raw=%r", head, mcode,
                        )

        elif url == "/button/status" and module == "BUTTON":
            normalised = _normalise_button_event(typ)
            if normalised:
                self._pending_events.append(
                    ("button", normalised, {"raw_type": str(typ), "info": info}),
                )
            else:
                _LOGGER.debug(
                    "V2 BUTTON push with unmapped type=%r info=%r — ignored",
                    typ, info,
                )

        elif url == "/device/config" and module == "DEVICE_CONFIG" and typ == "INFO":
            # Mirrors the BassXT push handler: when the device
            # broadcasts its config blob, latch every relevant field
            # into ``_latest`` so the next poll cycle pushes it into
            # ``XtoolDeviceState``.
            if isinstance(info, dict):
                _config_keys = (
                    ("flameAlarm",          "flame_alarm_v2_enabled"),
                    ("beepEnable",          "beep_enabled_v2"),
                    ("gapCheck",            "gap_check_enabled"),
                    ("machineLockCheck",    "machine_lock_check_enabled"),
                    ("autoSleepEnable",     "auto_sleep_enable"),
                    ("fillLightBrightFront","fill_light_a"),
                    ("fillLightBrightBack", "fill_light_b"),
                    # F2 family V2 firmware reuses ``purifierTimeout`` as
                    # the "Exhaust time after processing" knob — Studio's
                    # ``setFanSmokeExhaustTime`` writes it. Mirror into
                    # both legacy state fields so REST-era purifier
                    # entities and the V2 exhaust-fan entity both
                    # render correctly. (Issue #4 v2.5.4 retest.)
                    ("purifierTimeout",     "purifier_timeout"),
                    ("purifierTimeout",     "smoking_fan_duration"),
                    ("workingMode",         "working_mode"),
                    ("airAssistDelay",      "air_assist_close_delay"),
                    ("airassistCut",        "air_assist_gear_cut"),
                    ("airassistGrave",      "air_assist_gear_grave"),
                    ("sleepTimeout",        "sleep_timeout"),
                    ("sleepTimeoutOpenGap", "sleep_timeout_open_gap"),
                    ("printToolType",       "print_tool_type"),
                    # TODO v2.5.5 — entity scaffolding deferred:
                    # ("gapCheckWithKey",            "gap_check_with_key"),
                    # ("globalOffsetZ",              "global_offset_z"),
                    # ("innerZOffset",               "inner_z_offset"),
                    # ("secondOffsetFlag",           "second_offset_flag"),
                    # ("zPositionCompensateSmall",   "z_position_compensate_small"),
                )
                for src, dst in _config_keys:
                    if src in info:
                        self._latest[dst] = info[src]
                # ``workingMode`` is an enum string on F-series V2
                # firmware. **Polarity confirmed inverted via Issue #4
                # v2.5.4 retest**: ``NORMAL`` = Stops-when-moved on,
                # ``HANDLE`` = off (HANDLE means handheld override).
                # = off). Surface it as a bool mirror so the
                # ``stops_when_moved`` switch can render its toggle.
                if "workingMode" in info:
                    wm = str(info["workingMode"] or "").upper()
                    self._latest["stops_when_moved"] = wm == "NORMAL"

        elif url.startswith("/peripheral/"):
            ptype = url.removeprefix("/peripheral/")
            self._apply_peripheral_push(ptype, data, info, typ)

        else:
            _LOGGER.debug("V2 unhandled push event: %s", event)

    def _maybe_emit_job_event(
        self,
        event_kind: str,
        task_id: str,
        attrs: dict[str, Any] | None,
    ) -> None:
        """Emit a Job lifecycle event from the push handler, deduping
        against repeat firings for the same task within one job cycle.

        The coordinator's poll-cycle transition detector still handles
        ``paused`` / ``resumed`` / ``cancelled`` (those are slow user
        actions that survive a 5-s poll gap). Push-driven
        ``started`` / ``finished`` / ``framing_*`` lives here so fast
        jobs that complete within a single poll cycle still surface a
        Job event.
        """
        key = (task_id, event_kind)
        if self._last_push_job_event == key:
            return
        self._last_push_job_event = key
        self._pending_events.append(("job", event_kind, attrs))

    def _apply_peripheral_push(
        self,
        ptype: str,
        data: dict[str, Any],
        info: Any,
        typ: Any,
    ) -> None:
        """Translate a `/peripheral/<type>` push into a `_latest` cache key.

        Pattern across V2 firmware: peripheral push events carry their
        boolean / numeric / text state in `info` (often as a string
        like ``"on"``/``"off"``). Each peripheral-type maps to one
        ``XtoolDeviceState`` field in the table below.
        """
        is_on = None
        if isinstance(info, str):
            is_on = info.lower() == "on"
        elif isinstance(info, bool):
            is_on = info
        elif isinstance(info, dict):
            v = info.get("state") or info.get("value")
            if isinstance(v, str):
                is_on = v.lower() == "on"
            elif isinstance(v, bool):
                is_on = v

        # Boolean peripherals — straight passthrough
        bool_map = {
            "cooling_fan":     "cooling_fan_running",
            "smoking_fan":     "smoking_fan_running",
            "uv_fire_sensor":  "uv_fire_alarm",
            "water_pump":      "water_pump_running",
            "water_line":      "water_line_ok",
            "drawer":          "drawer_open",
            "cpu_fan":         "cpu_fan_running",
        }
        if ptype in bool_map and is_on is not None:
            self._latest[bool_map[ptype]] = is_on
            return

        # Numeric peripherals — value in info.value / info.temp / info.flow
        if isinstance(info, dict):
            if ptype == "water_tmp":
                v = info.get("temp") or info.get("value")
                if isinstance(v, (int, float)):
                    self._latest["water_temperature"] = float(v)
            elif ptype == "water_flow":
                v = info.get("flow") or info.get("value")
                if isinstance(v, (int, float)):
                    self._latest["water_flow"] = float(v)
            elif ptype == "digital_screen":
                v = info.get("brightness") or info.get("value")
                if isinstance(v, (int, float)):
                    self._latest["display_brightness"] = int(v)
            elif ptype == "gyro":
                for axis in ("x", "y", "z"):
                    v = info.get(axis)
                    if isinstance(v, (int, float)):
                        self._latest[f"gyro_{axis}"] = float(v)
            elif ptype == "laser_head":
                for src, dst in (("x", "position_x"), ("y", "position_y"),
                                 ("z", "position_z")):
                    v = info.get(src)
                    if isinstance(v, (int, float)):
                        self._latest[dst] = float(v)
            elif ptype == "ir_measure_distance":
                v = info.get("distance") or info.get("value")
                if isinstance(v, (int, float)):
                    self._latest["last_distance_mm"] = float(v)
            elif ptype == "fill_light":
                # Dual-channel push: ``{front: N, back: N}`` on F2
                # family. Legacy single-channel models still send
                # ``{brightness: N}`` (mirror onto both channels).
                front = info.get("front")
                back = info.get("back")
                if isinstance(front, (int, float)):
                    self._latest["fill_light_a"] = int(front)
                if isinstance(back, (int, float)):
                    self._latest["fill_light_b"] = int(back)
                if front is None and back is None:
                    v = info.get("brightness") or info.get("value")
                    if isinstance(v, (int, float)):
                        self._latest["fill_light_a"] = int(v)
                        self._latest["fill_light_b"] = int(v)
            else:
                _LOGGER.debug(
                    "V2 unhandled peripheral push type=%r info=%r — please "
                    "report so the field can be wired through",
                    ptype, info,
                )

    # ── XtoolProtocol contract ────────────────────────────────────────

    async def get_version(self) -> str | None:
        try:
            data = await self.request(self.PATH_DEVICE_INFO, "GET")
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
            data = await self.request(self.PATH_DEVICE_INFO, "GET")
        except Exception:
            data = {}
        _LOGGER.debug("V2 %s raw response: %s", self.PATH_DEVICE_INFO, data)
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
                    or firmware.get("master_rk3568_mainservice")
                    or firmware.get("master_h3_laserservice")
                    or ""
                )
            laser_power = data.get("laserPower")
            if isinstance(laser_power, list) and laser_power:
                try:
                    watts = int(laser_power[0])
                except (TypeError, ValueError):
                    watts = 0
                # ``laser_power_watts`` is a read-only property on
                # ``DeviceInfo`` (returns ``self.laser.power_watts``).
                # Set the underlying ``laser`` field — the property
                # picks the value up automatically.
                info.laser = LaserInfo(power_watts=watts)

        # MetalFab firmware returns an empty body for the GET — the
        # real machineInfo arrives via the `/device/info MACHINE_INFO
        # INFO` push event after the WS opens. Fall back to whatever
        # the push handler has already cached in ``_latest``.
        if not info.device_name:
            info.device_name = self._latest.get("device_name", "")
        if not info.serial_number:
            info.serial_number = self._latest.get("serial_number", "")
        if not info.mac_address:
            info.mac_address = self._latest.get("mac_address", "")
        if not info.main_firmware:
            info.main_firmware = self._latest.get("firmware_version", "")

        # Cache for later poll cycles
        self._latest["device_name"] = info.device_name
        self._latest["serial_number"] = info.serial_number
        self._latest["firmware_version"] = info.main_firmware
        if info.mac_address:
            self._latest["mac_address"] = info.mac_address
        return info

    async def _poll_runtime_status(self, state: XtoolDeviceState) -> None:
        """Fetch + apply the device runtime / curMode block.

        Default URL is ``/v1/device/runtime-infos`` (F1/F2 norm). The
        response shape ``{curMode:{mode, subMode, taskId}}`` is shared
        across every V2-capable model — only the URL diverges, so
        subclasses override ``PATH_RUNTIME_INFOS`` rather than the
        whole method.
        """
        try:
            rt = await self.request(self.PATH_RUNTIME_INFOS, "GET")
        except Exception as err:
            _LOGGER.debug("V2 %s failed: %s", self.PATH_RUNTIME_INFOS, err)
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

    async def _poll_configs(self) -> None:
        """Fetch + apply the persistent config blob.

        Default uses ``GET /v1/device/configs`` (F1/F2 norm) which
        returns the full kv dict. P2S V2 firmware diverges: it uses
        ``GET /v1/config/get`` with a body that lists which keys to
        return — that override lives in :class:`P2SWSV2Protocol`.
        """
        try:
            cfg = await self.request(self.PATH_CONFIGS_GET, "GET")
        except Exception:
            cfg = None
        if isinstance(cfg, dict):
            self._apply_configs(cfg)

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Refresh state via V2 request endpoints + push cache."""
        if not self._connected:
            await self.connect()

        # 1. Runtime state — work mode + subMode + taskId. ``PATH_RUNTIME_INFOS``
        # is overridable: P2S V2 firmware swaps it for
        # ``/v1/device/runningStatus`` (same response shape).
        await self._poll_runtime_status(state)

        # 2. Peripheral aggregate via /v1/peripheral/param?type=...
        # Build the type list dynamically from the model's capability
        # flags so we don't hammer the device with queries it has no
        # peripheral for. The always-on baseline is restricted to
        # types that work without an ``action`` body on every V2
        # firmware variant we've seen — `cooling_fan`, `smoking_fan`
        # and `airassistV2` are NOT in that set (HJ003 / GS003 reject
        # them with `code -3 error action type !`).
        model = getattr(self, "_model", None)
        ptypes: list[tuple[str, dict[str, Any] | None]] = [
            ("gap", None),
            ("machine_lock", None),
        ]
        if model is not None:
            if getattr(model, "has_drawer", False):
                ptypes.append(("drawer", None))
            if getattr(model, "has_uv_fire", False):
                ptypes.append(("uv_fire_sensor", None))
            if getattr(model, "has_water_cooling", False):
                ptypes.extend([
                    ("water_pump", None),
                    ("water_line", None),
                    ("water_tmp", None),
                    ("water_flow", None),
                ])
            if getattr(model, "has_gyro", False):
                ptypes.append(("gyro", None))
            if (
                getattr(model, "has_laser_head_position", False)
                or getattr(model, "has_z_axis", False)
            ):
                # ``laser_head`` requires an explicit action — Studio
                # bundles always send `data:{action:"get_coord"}` for
                # state queries, plain GET returns `code 1: failed`.
                ptypes.append(("laser_head", {"action": "get_coord"}))
            if getattr(model, "has_distance_measure", False):
                ptypes.append(("ir_measure_distance", None))
            if getattr(model, "has_display_screen", False):
                ptypes.append(("digital_screen", None))
            if getattr(model, "has_fill_light", False):
                ptypes.append(("fill_light", None))
            if getattr(model, "has_ir_led", False):
                # Studio's ``controlRedLed`` reads the IR LED state
                # via ``/v1/peripheral/param?type=ir_led`` with
                # ``index:"global"``. Without this poll the Red dot
                # entity defaults to off even when the firmware
                # boots with it lit.
                ptypes.append(("ir_led", {"index": "global"}))
            if getattr(model, "has_purifier_timeout", False) or \
               getattr(model, "has_air_assist_state", False):
                ptypes.append(("ext_purifier", None))

        for ptype, body in ptypes:
            if ptype in self._unsupported_peripheral_types:
                continue
            try:
                p = await self.request(
                    "/v1/peripheral/param", "GET",
                    params={"type": ptype},
                    data=body,
                )
            except RuntimeError as err:
                msg = str(err)
                # Firmware-side rejection — type is not exposed on this
                # model. Cache it so we stop retrying every poll. The
                # set is reset on each connect so a firmware upgrade
                # gets re-probed.
                if (
                    "code -3" in msg
                    or "code -2" in msg
                    or "code 10" in msg
                    or "code 1:" in msg
                ):
                    _LOGGER.debug(
                        "V2 peripheral type=%s rejected by firmware "
                        "(%s) — suppressing for this connection",
                        ptype, msg,
                    )
                    self._unsupported_peripheral_types.add(ptype)
                else:
                    _LOGGER.debug(
                        "V2 /v1/peripheral/param type=%s failed: %s",
                        ptype, err,
                    )
                continue
            except Exception as err:
                _LOGGER.debug(
                    "V2 /v1/peripheral/param type=%s failed: %s",
                    ptype, err,
                )
                continue
            _LOGGER.debug("V2 /v1/peripheral/param type=%s raw: %s", ptype, p)
            if not isinstance(p, dict):
                continue
            self._apply_peripheral_param(ptype, p, state)

        # 3. Configs — full config blob, slow cadence (every 6 polls ≈
        # every 30 s at 5 s base interval). Push events keep the values
        # fresh between polls. ``_poll_configs`` is overridable: P2S V2
        # firmware uses ``/v1/config/get`` with a kv-list body.
        if self._poll_counter % 6 == 0:
            await self._poll_configs()

        # 4. Statistics — lifetime counters, slow cadence. Skip on every
        # poll once cached as unsupported (GS006, P2S, DT001 don't
        # expose ``/v1/device/statistics``).
        if (
            self._poll_counter % 12 == 0
            and self.PATH_STATISTICS not in self._unsupported_endpoints
        ):
            try:
                stats = await self.request(self.PATH_STATISTICS, "GET")
            except RuntimeError as err:
                msg = str(err)
                if any(c in msg for c in ("code -2", "code -3", "code 10", "code 1:", "code 404")):
                    _LOGGER.debug(
                        "V2 %s rejected by firmware (%s) — caching as unsupported",
                        self.PATH_STATISTICS, msg,
                    )
                    self._unsupported_endpoints.add(self.PATH_STATISTICS)
                stats = None
            except Exception:
                stats = None
            _LOGGER.debug("V2 %s raw: %s", self.PATH_STATISTICS, stats)
            if isinstance(stats, dict):
                # Modern firmware (HJ003 / GS003 / F1U …) returns these
                # exact field names per the Studio bundle's
                # ``deviceInfo``/``getWorkingTime`` transformResult.
                # `timeSystemWork` carries the device's total powered-on
                # time; subtracting `timeModeWorking` gives standby.
                v = stats.get("timeModeWorking")
                if isinstance(v, (int, float)):
                    try:
                        self._latest["working_seconds"] = int(v)
                    except (TypeError, ValueError):
                        pass
                tsw = stats.get("timeSystemWork")
                tmw = stats.get("timeModeWorking")
                if (
                    isinstance(tsw, (int, float))
                    and isinstance(tmw, (int, float))
                ):
                    try:
                        self._latest["standby_seconds"] = max(0, int(tsw) - int(tmw))
                    except (TypeError, ValueError):
                        pass
                online = stats.get("numOnlineWorking")
                offline = stats.get("numOfflineWorking")
                if (
                    isinstance(online, (int, float))
                    or isinstance(offline, (int, float))
                ):
                    try:
                        self._latest["session_count"] = (
                            int(online or 0) + int(offline or 0)
                        )
                    except (TypeError, ValueError):
                        pass
                # Legacy field names — kept as fallback for older
                # firmware revisions whose bundles pre-date the rename.
                for src, dst in (
                    ("workingTime",     "working_seconds"),
                    ("sessionCount",    "session_count"),
                    ("standbyTime",     "standby_seconds"),
                    ("toolRuntime",     "tool_runtime_seconds"),
                    ("totalWorkTime",   "working_seconds"),
                    ("totalStandbyTime", "standby_seconds"),
                    ("toolUseTime",     "tool_runtime_seconds"),
                    ("workCount",       "session_count"),
                ):
                    v = stats.get(src)
                    if isinstance(v, (int, float)):
                        self._latest[dst] = int(v)

        # 5. Progress — only when a job is running.
        if state.status in (XtoolStatus.PROCESSING,
                             XtoolStatus.PROCESSING_READY,
                             XtoolStatus.FRAMING):
            try:
                prog = await self.request(self.PATH_PROGRESS, "GET")
            except Exception:
                prog = None
            if isinstance(prog, dict):
                wt = prog.get("workingTime") or prog.get("totalTime")
                if isinstance(wt, (int, float)):
                    state.task_time = int(wt)

        # 6. Alarms — alarm presence, slow cadence. Skip once cached as
        # unsupported (F1 / GS005 / HJ003 / M1Ultra / P2S / P3 / DT001
        # don't expose ``/v1/device/alarms`` — alarm transitions still
        # arrive via push frames `/temperature/alarm`, `/laser_head/alarm`
        # etc.).
        if (
            self._poll_counter % 6 == 0
            and self.PATH_ALARMS not in self._unsupported_endpoints
        ):
            try:
                alarms = await self.request(self.PATH_ALARMS, "GET")
            except RuntimeError as err:
                msg = str(err)
                if any(c in msg for c in ("code -2", "code -3", "code 10", "code 1:", "code 404")):
                    _LOGGER.debug(
                        "V2 %s rejected by firmware (%s) — caching as unsupported",
                        self.PATH_ALARMS, msg,
                    )
                    self._unsupported_endpoints.add(self.PATH_ALARMS)
                alarms = None
            except Exception:
                alarms = None
            if isinstance(alarms, dict):
                lst = alarms.get("alarms") or alarms.get("data") or []
                self._latest["alarm_present"] = bool(lst)
            elif isinstance(alarms, list):
                self._latest["alarm_present"] = bool(alarms)

        self._poll_counter += 1

        # 7. Push-cached values (overrule poll if newer)
        self._apply_latest_to_state(state)

        _LOGGER.debug(
            "V2 poll resolved: status=%s task_id=%r task_time=%s "
            "working_mode=%r cover_open=%s drawer_open=%s "
            "machine_lock=%s air_assist=%s "
            "position=(%s,%s,%s) gyro=(%s,%s,%s) water=(%s°C,%s) "
            "alarm=%s",
            state.status, state.task_id, state.task_time,
            state.working_mode, state.cover_open, state.drawer_open,
            state.machine_lock, state.air_assist_connected,
            state.position_x, state.position_y, state.position_z,
            state.gyro_x, state.gyro_y, state.gyro_z,
            state.water_temperature, state.water_flow,
            state.alarm_present,
        )

    def _apply_peripheral_param(
        self,
        ptype: str,
        p: dict[str, Any],
        state: XtoolDeviceState,
    ) -> None:
        """Translate a `/v1/peripheral/param?type=<X>` GET response into
        ``state`` field assignments. ``state`` field is the canonical
        target — ``_latest`` is bypassed because we're already inside
        the active poll cycle."""
        # F2 Ultra UV firmware (40.130.021.00.ht2) returns gap state as
        # ``{stateMCU, stateRK3562}`` — the cover sensor is read by both
        # the safety-MCU and the application processor. Fall back to the
        # MCU value when the unified ``state`` field is missing.
        st = p.get("state") or p.get("stateMCU") or p.get("stateRK3562")
        is_on = st == "on" if isinstance(st, str) else None
        is_off = st == "off" if isinstance(st, str) else None

        if ptype == "gap":
            state.cover_open = is_on
        elif ptype == "machine_lock":
            # USB safety-key presence (Studio's UsbKeyLockStatus).
            # state="on" = key inserted → True (plugged in).
            state.machine_lock = is_on
        elif ptype == "airassistV2":
            state.air_assist_connected = is_on
        elif ptype == "drawer":
            state.drawer_open = is_on
        elif ptype == "cooling_fan":
            state.cooling_fan_running = is_on
        elif ptype == "smoking_fan":
            state.smoking_fan_running = is_on
        elif ptype == "uv_fire_sensor":
            state.uv_fire_alarm = is_on
        elif ptype == "water_pump":
            state.water_pump_running = is_on
        elif ptype == "water_line":
            state.water_line_ok = is_on
        elif ptype == "water_tmp":
            v = p.get("temp") or p.get("value")
            if isinstance(v, (int, float)):
                state.water_temperature = float(v)
        elif ptype == "water_flow":
            v = p.get("flow") or p.get("value")
            if isinstance(v, (int, float)):
                state.water_flow = float(v)
        elif ptype == "gyro":
            for axis in ("x", "y", "z"):
                v = p.get(axis)
                if isinstance(v, (int, float)):
                    setattr(state, f"gyro_{axis}", float(v))
        elif ptype == "laser_head":
            for src, dst in (("x", "position_x"), ("y", "position_y"),
                             ("z", "position_z")):
                v = p.get(src)
                if isinstance(v, (int, float)):
                    setattr(state, dst, float(v))
        elif ptype == "ir_measure_distance":
            v = p.get("distance") or p.get("value")
            if isinstance(v, (int, float)):
                state.last_distance_mm = float(v)
        elif ptype == "digital_screen":
            v = p.get("brightness") or p.get("value")
            if isinstance(v, (int, float)):
                state.display_brightness = int(v)
        elif ptype == "fill_light":
            # F-family V2 firmware returns ``{back, front}``; legacy
            # single-channel models still return ``{brightness}``.
            front = p.get("front")
            back = p.get("back")
            if isinstance(front, (int, float)):
                state.fill_light_a = int(front)
            if isinstance(back, (int, float)):
                state.fill_light_b = int(back)
            if front is None and back is None:
                v = p.get("brightness") or p.get("value")
                if isinstance(v, (int, float)):
                    state.fill_light_a = int(v)
                    state.fill_light_b = int(v)
        elif ptype == "ext_purifier":
            v = p.get("speed") or p.get("value")
            if isinstance(v, (int, float)):
                state.purifier_speed = int(v)
                state.purifier_on = state.purifier_speed > 0
        elif ptype == "ir_led":
            # Single LED array on V2 hardware (see ``has_ir_led``
            # entity comment). Shape mirrors ``gap``: ``state="on"``
            # / ``"off"``. Mirror to both ``ir_led_global`` and
            # ``ir_led_close`` so future ``closeup`` index queries
            # don't desync.
            if is_on is True:
                state.ir_led_global = True
                state.ir_led_close = True
            elif is_off is True:
                state.ir_led_global = False
                state.ir_led_close = False
        else:
            _LOGGER.debug(
                "V2 peripheral/param type=%r returned shape we don't parse: "
                "%r — please report",
                ptype, p,
            )

    def _apply_configs(self, cfg: dict[str, Any]) -> None:
        """Latch `/v1/device/configs` GET response keys into `_latest`.

        The response usually nests the key/value blob under
        ``cfg["data"]["kv"]`` or directly under ``cfg["kv"]``; tolerate
        both shapes plus a top-level dict for older firmware.
        """
        kv = cfg
        if isinstance(cfg.get("kv"), dict):
            kv = cfg["kv"]
        elif isinstance(cfg.get("data"), dict):
            inner = cfg["data"]
            kv = inner.get("kv") if isinstance(inner.get("kv"), dict) else inner
        if not isinstance(kv, dict):
            return
        _config_keys = (
            ("flameAlarm",          "flame_alarm_v2_enabled"),
            ("beepEnable",          "beep_enabled_v2"),
            ("gapCheck",            "gap_check_enabled"),
            ("machineLockCheck",    "machine_lock_check_enabled"),
            ("autoSleepEnable",     "auto_sleep_enable"),
            ("fillLightBrightFront","fill_light_a"),
            ("fillLightBrightBack", "fill_light_b"),
            # See DEVICE_CONFIG push fan-out above — ``purifierTimeout``
            # doubles as the F2 family exhaust-fan post-run timer.
            ("purifierTimeout",     "purifier_timeout"),
            ("purifierTimeout",     "smoking_fan_duration"),
            ("workingMode",         "working_mode"),
            ("airAssistDelay",      "air_assist_close_delay"),
            ("airassistCut",        "air_assist_gear_cut"),
            ("airassistGrave",      "air_assist_gear_grave"),
            ("sleepTimeout",        "sleep_timeout"),
            ("sleepTimeoutOpenGap", "sleep_timeout_open_gap"),
            ("printToolType",       "print_tool_type"),
            # TODO v2.5.5 — entity scaffolding deferred:
            # ("gapCheckWithKey",            "gap_check_with_key"),
            # ("globalOffsetZ",              "global_offset_z"),
            # ("innerZOffset",               "inner_z_offset"),
            # ("secondOffsetFlag",           "second_offset_flag"),
            # ("zPositionCompensateSmall",   "z_position_compensate_small"),
        )
        for src, dst in _config_keys:
            if src in kv:
                self._latest[dst] = kv[src]
        # ``workingMode`` enum → ``stops_when_moved`` bool mirror.
        # Polarity: ``NORMAL`` = stationary / enforcement on,
        # ``HANDLE`` = handheld override / enforcement off.
        if "workingMode" in kv:
            wm = str(kv["workingMode"] or "").upper()
            self._latest["stops_when_moved"] = wm == "HANDLE"

        # Surface unknown keys at debug level so we can extend the map
        # when new firmware revisions add new persistent settings.
        known = {src for src, _ in _config_keys}
        unknown = sorted(k for k in kv.keys() if k not in known)
        if unknown:
            _LOGGER.debug(
                "V2 /v1/device/configs unknown keys (not yet wired): %s",
                ", ".join(unknown),
            )

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
                self.PATH_UPGRADE_MODE,
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
                self.PATH_UPGRADE_MODE,
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

    def _next_file_channel(self) -> int:
        """Pick a fresh channel ID for a file-transfer download."""
        self._file_channel_counter = (
            self._file_channel_counter + 1
        ) & WSV2_FILE_MAX_CHANNEL
        return self._file_channel_counter

    async def _download_file_stream(
        self,
        filename: str,
        file_type: int = 5,
        timeout: float = 30.0,
    ) -> bytes:
        """Download a file from the device via Studio's sliding-window protocol.

        Verified against Studio v1.7.23 ``main.e_TJj9fA.js``
        (``fileTransfer.buildFileTransfer`` +
        ``FileDownloader.startDownload/handleFileData``,
        constants in ``Iv/Lv/Rv/zv/Bv/Vv``). Flow:

        1. Reserve a single-byte channel ID (``Rv.MAX_CHANNEL = 255``).
        2. Send ``PUT /v1/filetransfer/download`` on the
           **instruction** channel via :meth:`request` with body
           ``{filetype, filename, digesttype:1 (MD5), channel,
           packetsize:<default>}``. Response payload is
           ``{filesize, digesttype, digestdata:<md5>, packetsize}``
           — firmware may return a smaller ``packetsize`` than we
           asked for; respect that.
        3. Open a fresh ``?function=file_stream`` WS.
        4. Send a binary ``FILE_REQUEST`` packet (10 bytes) on the
           file_stream WS, wrapped with envelope
           ``protocol_type=FILE_TRANSFER``:

                byte 0     : opcode = 1 (FILE_REQUEST)
                byte 1     : channel
                bytes 2-6  : offset (5-byte big-endian)
                bytes 7-9  : windowSize (3-byte big-endian,
                             ``min(Iv=5 MiB, filesize - offset)``)

        5. Firmware replies with ``FILE_DATA`` packets on the
           file_stream WS, each envelope-wrapped with
           ``protocol_type=FILE_TRANSFER``:

                byte 0     : opcode = 129 (FILE_DATA)
                byte 1     : channel
                bytes 2-6  : offset (5-byte big-endian)
                bytes 7+   : raw file bytes at ``offset``

        6. When ``receivedSize`` in the current window equals the
           requested window, send the next ``FILE_REQUEST`` with
           the new offset. Repeat until ``receivedSize == filesize``.
        7. Verify ``md5(buffer) == handshake.digestdata``. On
           mismatch, skip finish and return empty (Studio's
           ``FileDownloader.handleFileData`` throws here).
        8. Send ``PUT /v1/filetransfer/finish`` on the instruction
           channel with body
           ``{code:0, message:"file transfer finish", channel}``.
        """
        # ---- Step 1 — reserve channel ------------------------------
        channel = self._next_file_channel()

        # ---- Step 2 — instruction-channel handshake ---------------
        try:
            handshake = await self.request(
                "/v1/filetransfer/download",
                "PUT",
                data={
                    "filetype": file_type,
                    "filename": filename,
                    "digesttype": WSV2_FILE_DIGEST_MD5,
                    "channel": channel,
                    "packetsize": WSV2_FILE_DEFAULT_PACKET,
                },
                timeout=timeout,
            )
        except Exception as err:
            _LOGGER.debug(
                "V2 file_stream /v1/filetransfer/download handshake "
                "failed for %s: %s", filename, err,
            )
            return b""

        if not isinstance(handshake, dict):
            _LOGGER.debug(
                "V2 file_stream handshake reply not a dict: %r", handshake,
            )
            return b""
        try:
            filesize = int(handshake.get("filesize") or 0)
        except (TypeError, ValueError):
            filesize = 0
        if filesize <= 0:
            _LOGGER.debug(
                "V2 file_stream handshake missing/zero filesize: %r",
                handshake,
            )
            return b""
        digest = handshake.get("digestdata") or ""
        packetsize = handshake.get("packetsize")
        window_cap = WSV2_FILE_DEFAULT_WINDOW
        if isinstance(packetsize, int) and packetsize > 0:
            window_cap = min(window_cap, packetsize)
        _LOGGER.debug(
            "V2 file_stream handshake OK filename=%s channel=%d "
            "filesize=%d packetsize=%s digest=%s",
            filename, channel, filesize, packetsize, digest,
        )

        # ---- Step 3 — open file_stream WS -------------------------
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        url = (
            f"wss://{self.host}:{self._port}{WSV2_PATH}"
            f"?id={uuid.uuid4()}&function=file_stream"
        )
        buffer = bytearray(filesize)
        received = 0
        window_requested = 0
        window_received = 0
        scan = bytearray()

        async with self._session.ws_connect(
            url,
            ssl=_ssl_context(),
            timeout=aiohttp.ClientTimeout(total=timeout),
            heartbeat=None,
            max_msg_size=0,
        ) as ws:

            def build_file_request(offset: int, size: int) -> bytes:
                packet = bytearray(WSV2_FILE_REQUEST_HEADER)
                packet[0] = WSV2_FILE_OP_REQUEST
                packet[1] = channel & 0xFF
                # 5-byte big-endian offset
                for i in range(5):
                    packet[2 + i] = (offset >> (8 * (4 - i))) & 0xFF
                # 3-byte big-endian window size
                for i in range(3):
                    packet[7 + i] = (size >> (8 * (2 - i))) & 0xFF
                # Studio calls sendCmd(..., calCrc16=false) for
                # FILE_REQUEST — bit 7 of the protocol_type byte
                # set, payload CRC zeroed. Match verbatim.
                return _encode_frame(
                    bytes(packet),
                    protocol_type=WSV2_PROTOCOL_FILE_TRANSFER,
                    cal_crc16=False,
                )

            async def request_window(offset: int) -> int:
                size = min(window_cap, filesize - offset)
                await ws.send_bytes(build_file_request(offset, size))
                _LOGGER.debug(
                    "V2 file_stream TX FILE_REQUEST channel=%d offset=%d "
                    "windowSize=%d",
                    channel, offset, size,
                )
                return size

            # ---- Step 4 — kick off first window --------------------
            window_requested = await request_window(0)
            deadline = time.monotonic() + timeout

            while received < filesize:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _LOGGER.debug(
                        "V2 file_stream download %s timed out after "
                        "%.1fs (received %d / %d bytes)",
                        filename, timeout, received, filesize,
                    )
                    break
                try:
                    msg = await asyncio.wait_for(
                        ws.receive(), timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    break

                if msg.type == aiohttp.WSMsgType.BINARY:
                    scan.extend(msg.data)
                    frames, scan_rem = _decode_frames(bytes(scan))
                    scan = bytearray(scan_rem)
                    for protocol_type, payload in frames:
                        if protocol_type != WSV2_PROTOCOL_FILE_TRANSFER:
                            _LOGGER.debug(
                                "V2 file_stream unexpected "
                                "protocol_type=%d len=%d",
                                protocol_type, len(payload),
                            )
                            continue
                        if len(payload) == 1 and payload[0] == 0x82:
                            # Pong / keep-alive.
                            continue
                        if len(payload) < WSV2_FILE_DATA_HEADER:
                            _LOGGER.debug(
                                "V2 file_stream short FILE_DATA payload "
                                "len=%d — ignored", len(payload),
                            )
                            continue
                        opcode = payload[0]
                        if opcode != WSV2_FILE_OP_DATA:
                            _LOGGER.debug(
                                "V2 file_stream unexpected opcode=%d "
                                "on channel=%d", opcode, payload[1],
                            )
                            continue
                        pkt_channel = payload[1]
                        if pkt_channel != (channel & 0xFF):
                            _LOGGER.debug(
                                "V2 file_stream FILE_DATA channel "
                                "mismatch got=%d want=%d",
                                pkt_channel, channel,
                            )
                            continue
                        offset = int.from_bytes(payload[2:7], "big")
                        content = payload[WSV2_FILE_DATA_HEADER:]
                        if offset + len(content) > filesize:
                            _LOGGER.debug(
                                "V2 file_stream FILE_DATA overrun "
                                "offset=%d len=%d filesize=%d — truncated",
                                offset, len(content), filesize,
                            )
                            content = content[: filesize - offset]
                        buffer[offset : offset + len(content)] = content
                        received += len(content)
                        window_received += len(content)
                    if (
                        window_received >= window_requested
                        and received < filesize
                    ):
                        window_received = 0
                        window_requested = await request_window(received)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    _LOGGER.debug(
                        "V2 file_stream unexpected TEXT frame: %s",
                        msg.data,
                    )
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break

        if received < filesize:
            _LOGGER.debug(
                "V2 file_stream download %s incomplete — %d / %d bytes",
                filename, received, filesize,
            )
            return b""

        # ---- Step 5 — MD5 verify vs handshake digestdata -----------
        # Studio's ``FileDownloader.handleFileData`` verifies the
        # reassembled buffer against ``digestdata`` from the
        # handshake before sending ``filetransfer/finish``. On
        # mismatch Studio throws ``Error("File MD5 verification
        # failed")`` and skips ``finish`` entirely so firmware
        # doesn't mark the transfer complete on our end. Mirror
        # that: log expected/actual, skip finish, return empty.
        if digest:
            actual = hashlib.md5(buffer).hexdigest()
            if actual.lower() != str(digest).lower():
                _LOGGER.debug(
                    "V2 file_stream MD5 verification failed "
                    "expected=%s actual=%s filename=%s size=%d",
                    digest, actual, filename, received,
                )
                return b""

        # ---- Step 6 — best-effort finish --------------------------
        try:
            await self.request(
                "/v1/filetransfer/finish",
                "PUT",
                data={
                    "code": 0,
                    "message": "file transfer finish",
                    "channel": channel,
                },
                timeout=3.0,
            )
        except Exception:
            pass

        _LOGGER.debug(
            "V2 file_stream download %s finished — %d bytes (MD5 OK)",
            filename, received,
        )
        return bytes(buffer)

    def set_machine_type(self, machine_type: str) -> None:
        """Stash the model's firmware_machine_type for the next flash call."""
        self._pending_machine_type = machine_type

    # The REST-family hook is stubbed here so coordinators that share a
    # base coordinator class don't crash when calling it on a V2 device.
    def set_strategy(self, strategy: str) -> None:  # pragma: no cover
        self._pending_strategy = strategy

    def set_model(self, model) -> None:
        """Stash the resolved ``XtoolDeviceModel`` so ``poll_state``
        can gate per-model peripheral queries (water, gyro, drawer …).
        """
        self._pending_model = model
        self._model = model

    async def upload_accessory_firmware(
        self,
        accessory_type_id: str,
        blob: bytes,
        md5: str,
        filename: str,
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """Not yet implemented on WS-V2 firmware.

        V2 uses a different upload shape than the REST/S1/D-series
        ``/upload`` + ``/v1/parts/firmware/upgrade`` path: the blob
        rides the ``file_stream`` WS channel with
        ``params={fileType:2, fileName:<md5>}`` and the trigger goes
        through ``parts_control`` against
        ``/v1/platform/accessories/upgrade`` with
        ``{params:{id:<numeric-Te-type-id>}, data:{filename:<md5>}}``.

        Needs:
        - ``Te`` numeric type-id lookup table (Te enum from
          PROTOCOL.md — already documented).
        - File-stream client wiring (the integration already has
          ``_ws_file`` initialised; needs a ``send_binary`` helper
          that emits the chunked-upload framing Studio uses).
        - Progress reporting via the ``accessory.upgradeProgressInfo``
          push frame on the instruction channel.

        Leaving as a clean ``NotImplementedError`` until a V2-firmware
        owner can validate the flow end-to-end.
        """
        raise NotImplementedError(
            "Accessory firmware updates over WS-V2 are not yet "
            "implemented — feature flag pending hardware-side "
            "validation. Track issue in the integration repo."
        )
