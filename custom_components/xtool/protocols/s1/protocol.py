"""WebSocket M-code protocol for xTool S1 devices.

The S1 has two communication paths:
- WebSocket on port 8081: real-time push frames + request/response commands
- HTTP on port 8080: system queries, file upload, command fallback via POST /cmd

Push frames from the device (no request needed):
- M222 → work state changes
- M810 → job filename changes
- M340 → alarm state changes
- M313 → Z-probe readings
- M15  → fill light physical on/off state

The XCS desktop app can conflict with our WebSocket connection. When detected,
we switch to XCS Compatibility Mode (HTTP-only operation via POST /cmd).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
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
    parse_param_float,
    parse_param_int,
    parse_params,
    parse_quoted_string,
)


# --- S1 native M222 S-code → XtoolStatus map -------------------------------

_M222_STATUS_MAP: dict[int, XtoolStatus] = {
    0: XtoolStatus.INITIALIZING,
    1: XtoolStatus.IDLE,
    2: XtoolStatus.WIFI_SETUP,
    3: XtoolStatus.IDLE,
    4: XtoolStatus.ERROR_LIMIT,
    7: XtoolStatus.ERROR_LASER_MODULE,
    9: XtoolStatus.ERROR_LIMIT,
    10: XtoolStatus.MEASURING,
    11: XtoolStatus.FRAME_READY,
    12: XtoolStatus.FRAMING,
    13: XtoolStatus.PROCESSING_READY,
    14: XtoolStatus.PROCESSING,
    15: XtoolStatus.PAUSED,
    16: XtoolStatus.FIRMWARE_UPDATE,
    17: XtoolStatus.SLEEPING,
    18: XtoolStatus.CANCELLING,
    19: XtoolStatus.FINISHED,
    20: XtoolStatus.ERROR_LIMIT,
    21: XtoolStatus.ERROR_LASER_CONTROL,
    22: XtoolStatus.ERROR_LASER_MODULE,
    24: XtoolStatus.MEASURE_AREA,
}


# --- S1-owned ports ---------------------------------------------------------

S1_HTTP_PORT = 8080
S1_WS_PORT = 8081


# --- S1 M-code commands -----------------------------------------------------
# Common query commands (send as-is, device echoes back with parameters).

CMD_DEVICE_STATUS = "M222"
CMD_IP_ADDRESS = "M2002"
CMD_FULL_INFO = "M2003"
CMD_FILL_LIGHT = "M13"
CMD_FLAME_ALARM = "M340"
CMD_FIRE_LEVEL = "M343"
CMD_SMOKING_FAN = "M7"
CMD_AIR_ASSIST = "M15"
CMD_MOVE_STOP = "M318"
CMD_BEEPER = "M21"
CMD_AIR_ASSIST_DELAY = "M1099"
CMD_LASER_COORD = "M303"
CMD_TASK_ID = "M810"
CMD_TASK_TIME = "M815"
CMD_DEVICE_NAME = "M100"
CMD_POSITION = "M27"
CMD_XTOUCH_STATUS = "M362"
CMD_SD_CARD = "M321"
CMD_LIGHT_INTERFERENCE = "M2240"
CMD_Z_OFFSET = "M1113"
CMD_AIRFLOW_V2 = "M9009"
CMD_ACCESSORIES = "M1098"
CMD_LIFETIME_STATS = "M2008 A1"  # bare M2008 returns nothing — needs any param
CMD_LIGHT_ACTIVE = "M15"  # Also returns air assist status: A{enabled} S{gear}
CMD_PROBE_Z = "M313"
CMD_SERIAL_NUMBER = "M310"
CMD_FIRMWARE_VERSION = "M99"
CMD_LASER_INFO = "M116"
CMD_RISER_BASE = "M54"
CMD_WORKSPACE_DIMS = "M223"  # response: "M223 X<mm> Y<mm> Z<mm>"
CMD_FULL_STATE_PUSH = "M2211"  # triggers full-state push frames (cheaper than M2003)
CMD_AIRFLOW_PURIFIER = "M9039"  # AP2 air cleaner state (push frame + query trigger)
AP2_POLL_INTERVAL = 30  # seconds between explicit M9039 queries when AP2 is enabled

# Job control / homing
CMD_PAUSE_JOB = "M22 S1"
CMD_RESUME_JOB = "M22 S0"
CMD_ENTER_UPGRADE_MODE = "M22 S3"
CMD_CANCEL_JOB = "M108"
CMD_HOME_ALL = "M111 S7"
CMD_HOME_XY = "M111 S3"
CMD_HOME_Z = "M111 S2"

# HTTP system actions (GET /system?action=…)
HTTP_ACTION_VERSION = "version"
HTTP_ACTION_SOCKET_CONN = "socket_conn_num"
HTTP_ACTION_UPGRADE_PROGRESS = "get_upgrade_progress"

# Firmware upload endpoint (S1 multi-board flash)
HTTP_PATH_BURN = "/burn"

# XCS Compatibility Mode (S1-only)
XCS_KICK_LIMIT = 3  # disconnects within window to trigger XCS mode
XCS_KICK_WINDOW = 30.0  # seconds
XCS_RECOVERY_INTERVAL = 60.0
XCS_KICK_DETECTION_SECONDS = 10.0
STATS_POLL_INTERVAL = 300  # seconds between M2008 polls (5 minutes)

# M2003 JSON response keys
INFO_KEY_SERIAL_NUMBER = "M310"
INFO_KEY_DEVICE_NAME = "M100"
INFO_KEY_POWER_INFO = "M116"
INFO_KEY_MAIN_FIRMWARE = "M99"
INFO_KEY_LASER_FIRMWARE = "M1199"
INFO_KEY_WIFI_FIRMWARE = "M2099"
INFO_KEY_ACCESSORIES = "M1098"
INFO_KEY_FILL_LIGHT = "M13"
INFO_KEY_STATUS = "M222"
INFO_KEY_POSITION = "M27"

# M1098 accessories array positions
ACCESSORY_IDX_PURIFIER = 0
ACCESSORY_IDX_FIRE_EXTINGUISHER = 1
ACCESSORY_IDX_AIR_PUMP_V1 = 2
ACCESSORY_IDX_AIR_PUMP_V2 = 3
ACCESSORY_IDX_FIRE_EXTINGUISHER_V15 = 4

ACCESSORY_NAMES: dict[int, str] = {
    ACCESSORY_IDX_PURIFIER: "Purifier",
    ACCESSORY_IDX_FIRE_EXTINGUISHER: "Fire Extinguisher",
    ACCESSORY_IDX_AIR_PUMP_V1: "Air Pump 1.0",
    ACCESSORY_IDX_AIR_PUMP_V2: "Air Pump 2.0",
    ACCESSORY_IDX_FIRE_EXTINGUISHER_V15: "Fire Extinguisher v1.5",
}

# M54 riser-base codes
RISER_BASE_NAMES: dict[int, str] = {
    1: "Riser base",
    2: "Heightening kit",
}

# Laser module info parsed from M116 (X{type}Y{watts}B{producer}P{process}L{tube})
LASER_TYPE_NAMES: dict[int, str] = {
    0: "Diode",
    1: "Infrared",
}
LASER_POWERS_IR = {2, 3}


def get_laser_type_name(laser_type: int, power_watts: int) -> str:
    """Return the human-readable laser type."""
    if power_watts in LASER_POWERS_IR:
        return "Infrared"
    if power_watts > 0:
        return "Diode"
    return LASER_TYPE_NAMES.get(laser_type, "Unknown")


# --- S1-specific parsers (M2003 JSON, M116, M223, M1098) --------------------


def parse_laser_info(raw: str) -> LaserInfo:
    """Parse the M116 power-info string into LaserInfo."""
    laser = LaserInfo()
    if not raw:
        return laser
    params = parse_params(raw)
    for key, attr in (
        ("X", "laser_type"),
        ("Y", "power_watts"),
        ("B", "laser_producer"),
        ("P", "process_type"),
        ("L", "laser_tube"),
    ):
        try:
            setattr(laser, attr, int(params.get(key, "0")))
        except ValueError:
            pass
    return laser


def parse_m2003(raw: str) -> DeviceInfo:
    """Parse the M2003 JSON dump into a structured DeviceInfo."""
    info = DeviceInfo()
    json_str = raw.replace(CMD_FULL_INFO, "", 1).strip()
    if not json_str:
        return info
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return info

    info.serial_number = data.get(INFO_KEY_SERIAL_NUMBER, "")
    info.device_name = data.get(INFO_KEY_DEVICE_NAME, "")
    info.main_firmware = data.get(INFO_KEY_MAIN_FIRMWARE, "")
    info.laser_firmware = data.get(INFO_KEY_LASER_FIRMWARE, "")
    info.wifi_firmware = data.get(INFO_KEY_WIFI_FIRMWARE, "")
    info.laser = parse_laser_info(data.get(INFO_KEY_POWER_INFO, ""))
    return info


def parse_workspace_dims(raw: str) -> tuple[float, float, float]:
    """Parse 'M223 X… Y… Z…' into (x, y, z) mm tuple."""
    return (
        parse_param_float(raw, "X"),
        parse_param_float(raw, "Y"),
        parse_param_float(raw, "Z"),
    )


def parse_accessories(raw: str) -> list[str]:
    """Parse the M1098 quoted-string array into a list of firmware versions."""
    content = raw.replace(CMD_ACCESSORIES, "", 1).strip()
    if not content:
        return []
    return [part.strip().strip('"') for part in content.split(",")]

_LOGGER = logging.getLogger(__name__)

# M9039 (AP2 air cleaner) format:
#   M9039 [A|C]<speed> ... H<%> I<%> J<%> K<%> L<%> ... D<n> S<n>
# Where A1-A4 = running with speed n, C0/Cn = off. H/I/J/K/L are filter
# life percentages (pre / medium / activated carbon / dense carbon / hepa).
_M9039_SPEED_RE = re.compile(r"(?:^|\s)([AC])(\d+)")
_M9039_FILTERS_RE = re.compile(
    r"H(\d+).*?I(\d+).*?J(\d+).*?K(\d+).*?L(\d+)"
)
_M9039_D_RE = re.compile(r"(?:^|\s)D(\d+)")
_M9039_S_RE = re.compile(r"(?:^|\s)S(\d+)")


def _parse_m9039(body: str) -> dict[str, Any]:
    """Parse M9039 payload into AP2 state dict."""
    out: dict[str, Any] = {}
    speed_match = _M9039_SPEED_RE.search(body)
    if speed_match:
        prefix, num = speed_match.group(1), int(speed_match.group(2))
        out["purifier_speed"] = 0 if prefix == "C" else num
        out["purifier_on"] = prefix == "A"
    filters = _M9039_FILTERS_RE.search(body)
    if filters:
        out["filter_pre"] = int(filters.group(1))
        out["filter_medium"] = int(filters.group(2))
        out["filter_carbon"] = int(filters.group(3))
        out["filter_dense_carbon"] = int(filters.group(4))
        out["filter_hepa"] = int(filters.group(5))
    d_match = _M9039_D_RE.search(body)
    if d_match:
        out["purifier_sensor_d"] = int(d_match.group(1))
    s_match = _M9039_S_RE.search(body)
    if s_match:
        out["purifier_sensor_s"] = int(s_match.group(1))
    return out


class S1Protocol(XtoolProtocol):
    """WebSocket M-code protocol used by xTool S1.

    Supports push-based state updates and XCS Compatibility Mode.
    """

    def __init__(self, host: str, ws_port: int = S1_WS_PORT, http_port: int = S1_HTTP_PORT) -> None:
        """Initialize the protocol."""
        super().__init__(host)
        self._ws_port = ws_port
        self._http_port = http_port
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._lock = asyncio.Lock()

        # Push state cache (updated by background listener)
        self._push_state: dict[str, str] = {}

        # XCS Compatibility Mode tracking
        self._xcs_mode = False
        self._kick_times: list[float] = []
        self._ws_connect_time: float = 0.0
        self._last_xcs_recovery_attempt: float = 0.0

        # Rotating command order: failed commands get priority next cycle
        self._failed_cmds: list[str] = []

        # Lifetime stats slow poll
        self._last_stats_poll: float = -STATS_POLL_INTERVAL

        # AP2 air-cleaner state (parsed from M9039 push frames)
        self._ap2_state: dict[str, Any] = {}
        self._last_ap2_poll: float = -AP2_POLL_INTERVAL
        self._ap2_enabled: bool = False

    def set_ap2_enabled(self, enabled: bool) -> None:
        """Toggle AP2 polling. Called by coordinator from config option."""
        self._ap2_enabled = enabled

    @property
    def connected(self) -> bool:
        """Return True if the WebSocket is connected."""
        return self._ws is not None and not self._ws.closed

    @property
    def xcs_compatibility_mode(self) -> bool:
        """Return True if in XCS Compatibility Mode."""
        return self._xcs_mode

    @property
    def recently_kicked(self) -> bool:
        """Return True if we were kicked recently (even before full XCS mode)."""
        if not self._kick_times:
            return False
        return time.monotonic() - self._kick_times[-1] < XCS_KICK_WINDOW

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        if self.connected:
            return
        self._session = aiohttp.ClientSession()
        url = f"ws://{self.host}:{self._ws_port}/"
        try:
            self._ws = await self._session.ws_connect(url, timeout=10)
            self._ws_connect_time = time.monotonic()
            _LOGGER.debug("WebSocket connected to %s", url)
        except Exception:
            await self._close_session()
            raise

        # Kick a full-state push so all entities refresh quickly
        # without paying for an M2003 round-trip.
        try:
            await self._ws.send_str(CMD_FULL_STATE_PUSH + "\n")
        except Exception as err:
            _LOGGER.debug("M2211 trigger failed: %s", err)

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        await self._close_session()

    async def _close_session(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _track_kick(self) -> None:
        """Track a WebSocket kick by the remote end.

        If enough kicks accumulate within the window, enter XCS Compatibility Mode.
        """
        session_duration = time.monotonic() - self._ws_connect_time
        if self._ws_connect_time == 0:
            return

        now = time.monotonic()
        self._kick_times.append(now)
        self._kick_times = [t for t in self._kick_times if now - t < XCS_KICK_WINDOW]

        if session_duration < XCS_KICK_DETECTION_SECONDS:
            _LOGGER.debug(
                "WebSocket kicked after %.1fs (%d kicks in window)",
                session_duration,
                len(self._kick_times),
            )

        if len(self._kick_times) >= XCS_KICK_LIMIT and not self._xcs_mode:
            _LOGGER.warning(
                "Detected %d quick disconnects — entering XCS Compatibility Mode. "
                "The XCS desktop app is likely open.",
                len(self._kick_times),
            )
            self._xcs_mode = True

    async def send_command(self, command: str, timeout: float = 5.0) -> str:
        """Send a G-code command and return the matching response.

        In XCS Compatibility Mode, commands go via HTTP POST /cmd.
        If WS fails, automatically falls back to HTTP.
        """
        if self._xcs_mode:
            return await self._send_command_http(command, timeout)

        result = await self._send_command_ws(command, timeout)
        if result:
            return result

        # WS failed — if we just entered XCS mode, retry via HTTP
        if self._xcs_mode:
            return await self._send_command_http(command, timeout)

        # WS failed but not in XCS mode yet — try HTTP fallback once
        if self.recently_kicked:
            return await self._send_command_http(command, timeout)

        return ""

    async def _send_command_ws(self, command: str, timeout: float = 5.0) -> str:
        """Send command via WebSocket."""
        async with self._lock:
            if not self.connected:
                try:
                    await self.connect()
                except Exception:
                    return ""
            assert self._ws is not None

            cmd_prefix = command.split()[0]

            try:
                await self._ws.send_str(command + "\n")
            except Exception:
                self._track_kick()
                await self.disconnect()
                return ""

            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return ""
                try:
                    msg = await asyncio.wait_for(
                        self._ws.receive(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    return ""
                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    self._track_kick()
                    await self.disconnect()
                    return ""
                if msg.type == aiohttp.WSMsgType.TEXT:
                    response = msg.data.strip()
                    if response.startswith(cmd_prefix):
                        _LOGGER.debug("WS %s -> %s", command, response)
                        return response
                    self._handle_push_frame(response)

    async def _send_command_http(self, command: str, timeout: float = 5.0) -> str:
        """Send a command via HTTP POST /cmd fallback.

        NOTE: /cmd is fire-and-forget — the device always returns
        '{"result":"ok"}' regardless of what the M-code does. Actual
        responses (state values) come back via WebSocket push frames.
        Only use this path for write/control commands during XCS
        Compatibility Mode, never for queries.
        """
        url = f"http://{self.host}:{self._http_port}/cmd"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=command + "\n",
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    headers={"Content-Type": "text/plain"},
                ) as resp:
                    if resp.status == 200:
                        result = (await resp.text()).strip()
                        _LOGGER.debug("HTTP %s -> %s", command, result)
                        return result
        except Exception as err:
            _LOGGER.debug("HTTP command %s failed: %s", command, err)
        return ""

    def _handle_push_frame(self, text: str) -> None:
        """Process a push frame and cache the value."""
        _LOGGER.warning("XTOOL_PUSH: %r", text)  # TEMP cover-probe log
        if " " not in text:
            return
        head, _, tail = text.partition(" ")
        if head in (CMD_DEVICE_STATUS, CMD_TASK_ID, CMD_FLAME_ALARM, CMD_PROBE_Z, CMD_LIGHT_ACTIVE):
            self._push_state[head] = tail.strip()
        elif head == CMD_AIRFLOW_PURIFIER:
            parsed = _parse_m9039(tail.strip())
            if parsed:
                self._ap2_state.update(parsed)

    async def get_version(self) -> str | None:
        """Get firmware version via HTTP."""
        return await self._http_get(f"/system?action={HTTP_ACTION_VERSION}")

    async def get_device_info(self) -> DeviceInfo:
        """Get full device info via M2003 + M223 (workspace dimensions)."""
        info_raw = await self.send_command(CMD_FULL_INFO, timeout=5)
        info = parse_m2003(info_raw)
        name_raw = await self.send_command(CMD_DEVICE_NAME, timeout=5)
        name = parse_quoted_string(name_raw)
        if name:
            info.device_name = name
        # Workspace dims (static; queried once on first connect)
        try:
            dims_raw = await self.send_command(CMD_WORKSPACE_DIMS, timeout=3)
            if dims_raw:
                info.workspace_x, info.workspace_y, info.workspace_z = (
                    parse_workspace_dims(dims_raw)
                )
        except Exception as err:
            _LOGGER.debug("M223 workspace query failed: %s", err)
        return info

    async def get_connection_count(self) -> int:
        """Active client count via /system?action=socket_conn_num.

        Used by the S1 diagnostic sensor to display how many WS clients are
        currently connected (HA + XCS app + …).
        """
        result = await self._http_get(f"/system?action={HTTP_ACTION_SOCKET_CONN}")
        if result:
            try:
                return int(result)
            except ValueError:
                pass
        return 0

    async def check_http_heartbeat(self) -> bool:
        """Check if device is reachable via HTTP."""
        result = await self._http_get(f"/system?action={HTTP_ACTION_VERSION}")
        return result is not None

    async def try_xcs_recovery(self) -> bool:
        """Attempt to recover from XCS Compatibility Mode.

        Connects WS, sends a test command, waits, sends another.
        Only exits XCS mode if both succeed without getting kicked.
        """
        now = time.monotonic()
        if now - self._last_xcs_recovery_attempt < XCS_RECOVERY_INTERVAL:
            return False
        self._last_xcs_recovery_attempt = now

        try:
            await self.connect()
            # Send a test command and verify we get the right response
            result1 = await self._send_command_ws(CMD_DEVICE_STATUS, timeout=3)
            if not result1 or not self.connected:
                await self.disconnect()
                return False
            # Wait and test again to make sure we're not about to be kicked
            await asyncio.sleep(3)
            result2 = await self._send_command_ws(CMD_DEVICE_STATUS, timeout=3)
            if not result2 or not self.connected:
                await self.disconnect()
                return False
            _LOGGER.info("XCS Compatibility Mode recovery — switching back to normal WebSocket")
            self._xcs_mode = False
            self._kick_times.clear()
            return True
        except Exception:
            pass
        await self.disconnect()
        return False

    async def _http_get(self, path: str, timeout: float = 5.0) -> str | None:
        """Perform an HTTP GET request."""
        url = f"http://{self.host}:{self._http_port}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status == 200:
                        return (await resp.text()).strip()
        except Exception:
            pass
        return None

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Poll device state with rotating command order.

        Commands that failed in the previous cycle are sent first, ensuring
        all entities get updated over time even when the XCS app is causing
        intermittent WebSocket disconnects.

        Only successfully queried values are written to state — failed commands
        leave the previous value intact.
        """
        if self._xcs_mode:
            if not await self.check_http_heartbeat():
                raise ConnectionError("Device unreachable in XCS Compatibility Mode")
            await self.try_xcs_recovery()

        # Build command order: previously-failed commands first
        all_cmds = [
            CMD_DEVICE_STATUS, CMD_FILL_LIGHT, CMD_FLAME_ALARM, CMD_FIRE_LEVEL,
            CMD_SMOKING_FAN, CMD_BEEPER, CMD_MOVE_STOP, CMD_AIR_ASSIST_DELAY,
            CMD_LIGHT_ACTIVE, CMD_POSITION, CMD_TASK_ID, CMD_TASK_TIME,
            CMD_SD_CARD, CMD_ACCESSORIES, CMD_RISER_BASE,
        ]
        priority = [c for c in self._failed_cmds if c in all_cmds]
        rest = [c for c in all_cmds if c not in priority]
        ordered = priority + rest

        # Send queries and track failures
        responses: dict[str, str] = {}
        failed: list[str] = []
        for cmd in ordered:
            result = await self.send_command(cmd)
            responses[cmd] = result
            if not result:
                failed.append(cmd)
        self._failed_cmds = failed

        success_count = len(all_cmds) - len(failed)
        _LOGGER.debug(
            "Poll: %d/%d commands succeeded (failed: %s)",
            success_count, len(all_cmds),
            ", ".join(failed) if failed else "none",
        )

        # If ALL commands failed, this is a total connection loss
        if success_count == 0:
            raise ConnectionError("All commands failed — device unreachable")

        # Parse only successful responses — leave state fields unchanged for failures
        r = responses  # shorthand

        # Status (with push cache fallback)
        raw_status: int | None = None
        if r[CMD_DEVICE_STATUS]:
            raw_status = parse_param_int(r[CMD_DEVICE_STATUS], "S", -1)
        elif CMD_DEVICE_STATUS in self._push_state:
            raw_status = parse_param_int(
                f"{CMD_DEVICE_STATUS} {self._push_state[CMD_DEVICE_STATUS]}", "S", -1
            )
        if raw_status is not None and raw_status in _M222_STATUS_MAP:
            state.status = _M222_STATUS_MAP[raw_status]

        if r[CMD_FILL_LIGHT]:
            state.fill_light_a = parse_param_int(r[CMD_FILL_LIGHT], "A")
            state.fill_light_b = parse_param_int(r[CMD_FILL_LIGHT], "B")

        # Flame alarm (with push cache fallback)
        if r[CMD_FLAME_ALARM]:
            state.flame_alarm = parse_param_int(r[CMD_FLAME_ALARM], "A")
        elif CMD_FLAME_ALARM in self._push_state:
            state.flame_alarm = parse_param_int(f"{CMD_FLAME_ALARM} {self._push_state[CMD_FLAME_ALARM]}", "A")

        if r[CMD_FIRE_LEVEL]:
            state.fire_level = parse_param_int(r[CMD_FIRE_LEVEL], "S")

        if r[CMD_SMOKING_FAN]:
            state.smoking_fan_on = parse_param_int(r[CMD_SMOKING_FAN], "N") != 0
            state.smoking_fan_duration = parse_param_int(r[CMD_SMOKING_FAN], "D", 120)

        if r[CMD_BEEPER]:
            state.beeper_enabled = parse_param_int(r[CMD_BEEPER], "S") == 1

        if r[CMD_MOVE_STOP]:
            state.move_stop_enabled = parse_param_int(r[CMD_MOVE_STOP], "N") == 1

        if r[CMD_AIR_ASSIST_DELAY]:
            state.air_assist_close_delay = parse_param_int(r[CMD_AIR_ASSIST_DELAY], "T", 10)

        if r[CMD_POSITION]:
            state.position_x = parse_param_float(r[CMD_POSITION], "X")
            state.position_y = parse_param_float(r[CMD_POSITION], "Y")
            state.position_z = parse_param_float(r[CMD_POSITION], "Z")

        # Task ID (with push cache fallback)
        if r[CMD_TASK_ID]:
            task_id = parse_quoted_string(r[CMD_TASK_ID]) or ""
            state.task_id = "" if task_id == "NULL" else task_id
        elif CMD_TASK_ID in self._push_state:
            task_id = parse_quoted_string(f'{CMD_TASK_ID} {self._push_state[CMD_TASK_ID]}') or ""
            state.task_id = "" if task_id == "NULL" else task_id

        if r[CMD_TASK_TIME]:
            state.task_time = parse_param_int(r[CMD_TASK_TIME], "T")

        if r[CMD_SD_CARD]:
            state.sd_card_present = parse_param_int(r[CMD_SD_CARD], "S") != 0

        if r[CMD_ACCESSORIES]:
            state.accessories_raw = parse_accessories(r[CMD_ACCESSORIES])

        if r[CMD_RISER_BASE]:
            state.riser_base = parse_param_int(r[CMD_RISER_BASE], "T")

        # M15 dual purpose: air assist status (A/S values) + light active (push frames)
        if r[CMD_LIGHT_ACTIVE]:
            state.air_assist_enabled = parse_param_int(r[CMD_LIGHT_ACTIVE], "A") == 1
            state.air_assist_level = parse_param_int(r[CMD_LIGHT_ACTIVE], "S")

        # Light active: use push frame if available, otherwise infer from brightness
        if CMD_LIGHT_ACTIVE in self._push_state:
            state.light_active = parse_param_int(
                f"M15 {self._push_state[CMD_LIGHT_ACTIVE]}", "S"
            ) != 0
        else:
            state.light_active = state.fill_light_a > 0 or state.fill_light_b > 0

        # Push-driven AP2 air-cleaner state (parsed by _handle_push_frame)
        if self._ap2_state:
            for key, value in self._ap2_state.items():
                setattr(state, key, value)

        # Periodically request M9039 to refresh AP2 state when armed
        if self._ap2_enabled:
            now2 = time.monotonic()
            if now2 - self._last_ap2_poll >= AP2_POLL_INTERVAL:
                self._last_ap2_poll = now2
                try:
                    raw = await self.send_command(CMD_AIRFLOW_PURIFIER, timeout=4)
                    if raw and raw.startswith(CMD_AIRFLOW_PURIFIER):
                        body = raw[len(CMD_AIRFLOW_PURIFIER):].strip()
                        parsed = _parse_m9039(body)
                        if parsed:
                            self._ap2_state.update(parsed)
                            for key, value in parsed.items():
                                setattr(state, key, value)
                except Exception as err:
                    _LOGGER.debug("M9039 poll failed: %s", err)

        # Generic alarm flag (M340 != A0)
        if CMD_FLAME_ALARM in self._push_state:
            alarm_value = self._push_state[CMD_FLAME_ALARM]
            state.alarm_present = alarm_value not in ("A0", "0")

        # Slow poll: lifetime statistics (every 5 minutes)
        now = time.monotonic()
        if now - self._last_stats_poll >= STATS_POLL_INTERVAL:
            self._last_stats_poll = now
            try:
                stats_raw = await self.send_command(CMD_LIFETIME_STATS, timeout=8)
                if stats_raw:
                    state.working_seconds = parse_param_int(stats_raw, "A")
                    state.session_count = parse_param_int(stats_raw, "B")
                    state.standby_seconds = parse_param_int(stats_raw, "C")
                    state.tool_runtime_seconds = parse_param_int(stats_raw, "D")
            except Exception as err:
                _LOGGER.debug("M2008 poll failed: %s", err)



    # --- Firmware update overrides --------------------------------------

    async def get_firmware_versions(self, coordinator) -> dict[str, str]:
        """Per-board (M99 / M1199 / M2099) versions for cloud-update check."""
        model = coordinator.model
        if not model.firmware_multi_package or not model.firmware_board_ids:
            return (
                {"main": coordinator.firmware_version}
                if coordinator.firmware_version else {}
            )
        ids = model.firmware_board_ids
        fw_main = coordinator.firmware_version
        if not fw_main:
            return {}
        versions: dict[str, str] = {ids[0]: fw_main}
        cache = getattr(coordinator, "_device_info_cache", None)
        if cache is not None:
            if len(ids) > 1 and cache.laser_firmware:
                versions[ids[1]] = cache.laser_firmware
            if len(ids) > 2 and cache.wifi_firmware:
                versions[ids[2]] = cache.wifi_firmware
        # Fill missing slots with the main version as a best-effort default.
        for board_id in ids:
            versions.setdefault(board_id, fw_main)
        return versions

    async def flash_firmware(
        self,
        fw_file: FirmwareFile,
        data: bytes,
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """Multi-board flash via M22 S3 + POST /burn + /system?action= poll."""
        # Enter upgrade mode
        await self.send_command(CMD_ENTER_UPGRADE_MODE)

        url = f"http://{self.host}:{self._http_port}{HTTP_PATH_BURN}"
        form = aiohttp.FormData()
        form.add_field(
            "file", data,
            filename="mcu_firmware.bin",
            content_type="application/octet-stream",
        )
        form.add_field("burnType", fw_file.burn_type)

        async with aiohttp.ClientSession() as session:
            async def _upload() -> None:
                async with session.post(
                    url, data=form, timeout=aiohttp.ClientTimeout(total=600)
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(
                            f"Firmware upload failed: HTTP {resp.status}"
                        )

            poll_task = asyncio.create_task(self._poll_flash_progress(progress_cb))
            try:
                await _upload()
            finally:
                poll_task.cancel()
                try:
                    await poll_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _poll_flash_progress(
        self,
        progress_cb: Callable[[float], None] | None,
    ) -> None:
        """Poll /system?action=get_upgrade_progress and report real progress."""
        if progress_cb is None:
            return
        url = (
            f"http://{self.host}:{self._http_port}"
            f"/system?action={HTTP_ACTION_UPGRADE_PROGRESS}"
        )
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            payload = await resp.json(content_type=None)
                            curr = int(payload.get("curr_progress", 0))
                            total = int(payload.get("total_progress", 0))
                            if total > 0:
                                progress_cb(min(curr / total, 1.0))
                                if curr >= total:
                                    return
                except Exception as err:
                    _LOGGER.debug("Flash progress poll error: %s", err)
                await asyncio.sleep(2.0)
