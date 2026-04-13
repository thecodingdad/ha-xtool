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
import logging
import time

import aiohttp

from .const import (
    CMD_ACCESSORIES,
    CMD_AIR_ASSIST_DELAY,
    CMD_BEEPER,
    CMD_DEVICE_NAME,
    CMD_DEVICE_STATUS,
    CMD_FILL_LIGHT,
    CMD_FIRE_LEVEL,
    CMD_FLAME_ALARM,
    CMD_FULL_INFO,
    CMD_LIFETIME_STATS,
    CMD_LIGHT_ACTIVE,
    CMD_MOVE_STOP,
    CMD_POSITION,
    CMD_PROBE_Z,
    CMD_RISER_BASE,
    CMD_SD_CARD,
    CMD_SMOKING_FAN,
    CMD_TASK_ID,
    CMD_TASK_TIME,
    CMD_XTOUCH_STATUS,
    DEFAULT_HTTP_PORT,
    DEFAULT_WS_PORT,
    STATS_POLL_INTERVAL,
    XCS_KICK_DETECTION_SECONDS,
    XCS_KICK_LIMIT,
    XCS_KICK_WINDOW,
    XCS_RECOVERY_INTERVAL,
)
from .models import XtoolDeviceState
from .protocol import (
    DeviceInfo,
    XtoolProtocol,
    parse_accessories,
    parse_m2003,
    parse_param_float,
    parse_param_int,
    parse_quoted_string,
)

_LOGGER = logging.getLogger(__name__)


class WsMcodeProtocol(XtoolProtocol):
    """WebSocket M-code protocol used by xTool S1.

    Supports push-based state updates and XCS Compatibility Mode.
    """

    def __init__(self, host: str, ws_port: int = DEFAULT_WS_PORT, http_port: int = DEFAULT_HTTP_PORT) -> None:
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
        """Send a command via HTTP POST /cmd fallback."""
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
        if " " not in text:
            return
        head, _, tail = text.partition(" ")
        if head in (CMD_DEVICE_STATUS, CMD_TASK_ID, CMD_FLAME_ALARM, CMD_PROBE_Z, CMD_LIGHT_ACTIVE):
            self._push_state[head] = tail.strip()

    async def get_version(self) -> str | None:
        """Get firmware version via HTTP."""
        return await self._http_get("/system?action=version")

    async def get_device_info(self) -> DeviceInfo:
        """Get full device info via M2003."""
        info_raw = await self.send_command(CMD_FULL_INFO, timeout=5)
        info = parse_m2003(info_raw)
        name_raw = await self.send_command(CMD_DEVICE_NAME, timeout=5)
        name = parse_quoted_string(name_raw)
        if name:
            info.device_name = name
        return info

    async def get_connection_count(self) -> int:
        """Get active connection count via HTTP."""
        result = await self._http_get("/system?action=socket_conn_num")
        if result:
            try:
                return int(result)
            except ValueError:
                pass
        return 0

    async def check_http_heartbeat(self) -> bool:
        """Check if device is reachable via HTTP."""
        result = await self._http_get("/system?action=version")
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
            CMD_SD_CARD, CMD_XTOUCH_STATUS, CMD_ACCESSORIES, CMD_RISER_BASE,
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
        if r[CMD_DEVICE_STATUS]:
            state.status_code = parse_param_int(r[CMD_DEVICE_STATUS], "S", -1)
        elif CMD_DEVICE_STATUS in self._push_state:
            state.status_code = parse_param_int(f"{CMD_DEVICE_STATUS} {self._push_state[CMD_DEVICE_STATUS]}", "S", -1)

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

        if r[CMD_XTOUCH_STATUS]:
            state.xtouch_connected = parse_param_int(r[CMD_XTOUCH_STATUS], "S") == 1

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
