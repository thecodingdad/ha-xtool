"""HTTP M-code protocol for xTool D1/D1Pro/D1Pro 2.0 devices."""

from __future__ import annotations

import logging

import aiohttp

from .const import (
    CMD_AIR_ASSIST_DELAY,
    CMD_BEEPER,
    CMD_DEVICE_NAME,
    CMD_FILL_LIGHT,
    CMD_FIRMWARE_VERSION,
    CMD_FLAME_ALARM_D_SERIES,
    CMD_LASER_INFO,
    CMD_MOVE_STOP,
    CMD_POSITION_D_SERIES,
    CMD_SERIAL_NUMBER,
    CMD_STATUS_D_SERIES,
    DEFAULT_HTTP_PORT,
    STATUS_CODE_IDLE,
    STATUS_CODE_PROCESSING,
)
from .models import XtoolDeviceState
from .protocol import (
    DeviceInfo,
    XtoolProtocol,
    parse_laser_info,
    parse_param_float,
    parse_param_int,
    parse_quoted_string,
)

_LOGGER = logging.getLogger(__name__)


class HttpMcodeProtocol(XtoolProtocol):
    """HTTP M-code protocol used by xTool D1, D1Pro, D1Pro 2.0."""

    def __init__(self, host: str, port: int = DEFAULT_HTTP_PORT) -> None:
        """Initialize the protocol."""
        super().__init__(host)
        self._port = port
        self._base_url = f"http://{host}:{port}"

    @property
    def connected(self) -> bool:
        """HTTP is stateless — always 'connected'."""
        return True

    async def connect(self) -> None:
        """No persistent connection needed."""

    async def disconnect(self) -> None:
        """No persistent connection to close."""

    async def send_command(self, command: str, timeout: float = 5.0) -> str:
        """Send an M-code command via HTTP POST."""
        url = self._base_url
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=command,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    headers={"Content-Type": "text/plain"},
                ) as resp:
                    if resp.status == 200:
                        return (await resp.text()).strip()
        except Exception as err:
            _LOGGER.debug("HTTP M-code POST %s failed: %s", command, err)
        return ""

    async def get_version(self) -> str | None:
        """Get firmware version via M99 command."""
        result = await self.send_command(CMD_FIRMWARE_VERSION)
        return result if result else None

    async def get_device_info(self) -> DeviceInfo:
        """Get device info by sending multiple M-code queries."""
        info = DeviceInfo()

        # D-series uses individual M-code commands for device info
        version = await self.send_command(CMD_FIRMWARE_VERSION)
        m116 = await self.send_command(CMD_LASER_INFO)
        name_raw = await self.send_command(CMD_DEVICE_NAME)
        sn_raw = await self.send_command(CMD_SERIAL_NUMBER)

        info.main_firmware = version
        info.device_name = parse_quoted_string(name_raw) or ""
        info.serial_number = sn_raw.replace(CMD_SERIAL_NUMBER, "").strip().strip('"') if sn_raw else ""
        info.laser = parse_laser_info(m116.replace(CMD_LASER_INFO, "").strip())

        return info

    async def get_connection_count(self) -> int:
        """Not available via HTTP M-code."""
        return 0

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Poll all device state values and populate the state object."""
        # D-series status: M96 returns N0=idle, N1=working
        status_raw = await self.send_command(CMD_STATUS_D_SERIES)
        working = parse_param_int(status_raw, "N")
        state.status_code = STATUS_CODE_PROCESSING if working == 1 else STATUS_CODE_IDLE

        # Fill light
        light_raw = await self.send_command(CMD_FILL_LIGHT)
        # D-series M13 format might differ — try both param styles
        state.fill_light_a = parse_param_int(light_raw, "A")
        state.fill_light_b = parse_param_int(light_raw, "B")

        # Flame alarm: D-series uses M310 for setting, M309 for sensitivity
        flame_raw = await self.send_command(CMD_FLAME_ALARM_D_SERIES)
        if flame_raw:
            state.flame_alarm = parse_param_int(flame_raw, "S", 0)

        # Move stop: M318
        move_raw = await self.send_command(CMD_MOVE_STOP)
        state.move_stop_enabled = parse_param_int(move_raw, "N") == 1

        # Beeper: M21
        beeper_raw = await self.send_command(CMD_BEEPER)
        state.beeper_enabled = parse_param_int(beeper_raw, "S") == 1

        # Air assist delay: M1099
        delay_raw = await self.send_command(CMD_AIR_ASSIST_DELAY)
        state.air_assist_close_delay = parse_param_int(delay_raw, "T", 10)

        # Position
        pos_raw = await self.send_command(CMD_POSITION_D_SERIES)
        if pos_raw:
            state.position_x = parse_param_float(pos_raw, "X")
            state.position_y = parse_param_float(pos_raw, "Y")
