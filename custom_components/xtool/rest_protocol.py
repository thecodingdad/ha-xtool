"""REST API protocol for xTool F1/F1Ultra/P2/P2S/M1/M1Ultra/P1/GS models."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import BRIGHTNESS_DEVICE_MAX, BRIGHTNESS_HA_MAX, DEFAULT_HTTP_PORT
from .models import XtoolDeviceState
from .protocol import DeviceInfo, LaserInfo, XtoolProtocol

_LOGGER = logging.getLogger(__name__)


class RestProtocol(XtoolProtocol):
    """REST API protocol used by F1, F1Ultra, P2, P2S, M1, M1Ultra, P1, GS models."""

    def __init__(self, host: str, port: int = DEFAULT_HTTP_PORT) -> None:
        """Initialize the protocol."""
        super().__init__(host)
        self._port = port
        self._base_url = f"http://{host}:{port}"

    @property
    def connected(self) -> bool:
        """REST is stateless — always 'connected'."""
        return True

    async def connect(self) -> None:
        """No persistent connection needed for REST."""

    async def disconnect(self) -> None:
        """No persistent connection to close."""

    async def send_command(self, command: str, timeout: float = 5.0) -> str:
        """Send a command via /cnc/cmd endpoint (for G-code passthrough)."""
        return await self._post("/cnc/cmd", data={"cmd": command}, timeout=timeout) or ""

    async def get_version(self) -> str | None:
        """Get firmware version via /system endpoint."""
        data = await self._get_json("/system")
        if data and "firmware" in data:
            return data["firmware"]
        if data and "version" in data:
            return data["version"]
        return None

    async def get_device_info(self) -> DeviceInfo:
        """Get full device info via /device/machineInfo."""
        data = await self._get_json("/device/machineInfo")
        if not data:
            return DeviceInfo()

        # machineInfo returns: deviceName, mac, ip, sn, machineType, machineSubType,
        # laserPower, firmware, etc.
        power = 0
        try:
            power = int(data.get("laserPower", 0))
        except (ValueError, TypeError):
            pass

        firmware_str = ""
        firmware = data.get("firmware")
        if isinstance(firmware, list) and firmware:
            parts = [f.get("version", "") for f in firmware if f.get("version")]
            firmware_str = parts[0] if parts else ""
        elif isinstance(firmware, str):
            firmware_str = firmware

        return DeviceInfo(
            serial_number=data.get("sn", ""),
            device_name=data.get("deviceName", ""),
            laser=LaserInfo(power_watts=power),
            main_firmware=firmware_str,
        )

    async def get_connection_count(self) -> int:
        """Not available via REST API."""
        return 0

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Poll all device state values and populate the state object."""
        # Get status from /cnc/status or /device/runningStatus
        status_data = await self._get_json("/cnc/status")
        if status_data:
            state.status_code = _map_rest_status(status_data)
        else:
            running = await self._get_json("/device/runningStatus")
            if running and isinstance(running, dict):
                cur_mode = running.get("data", {}).get("curMode", {})
                state.status_code = _map_rest_mode(cur_mode.get("mode", ""))

        # Get processing progress
        progress = await self._get_json("/processing/progress")
        if progress and isinstance(progress, dict):
            state.task_time = progress.get("totalTime", 0)

        # Get fill light state
        light_data = await self._get_json("/peripheral/fill_light")
        if light_data and isinstance(light_data, dict):
            bri = light_data.get("value", 0)
            # REST uses 0-255, convert to 0-100
            level = round(bri * BRIGHTNESS_DEVICE_MAX / BRIGHTNESS_HA_MAX) if bri else 0
            state.fill_light_a = level
            state.fill_light_b = level

        # Get device working info
        work_info = await self._get_json("/device/workingInfo")
        if work_info and isinstance(work_info, dict):
            state.task_id = work_info.get("taskId", "")

        # Get flame alarm from config
        config = await self._post_json("/config/get", data={"keys": ["flameDetection"]})
        if config and isinstance(config, dict):
            flame = config.get("flameDetection", {})
            if isinstance(flame, dict):
                enabled = flame.get("enable", True)
                sensitivity = flame.get("sensitivity", "high")
                if not enabled:
                    state.flame_alarm = 2  # OFF
                elif sensitivity == "low":
                    state.flame_alarm = 1  # LOW
                else:
                    state.flame_alarm = 0  # HIGH

        # Drawer status (P2/P2S/M1Ultra)
        drawer = await self._get_json("/peripheral/drawer")
        if drawer and isinstance(drawer, dict):
            state.sd_card_present = False  # P2 doesn't have SD card the same way

    # --- REST Helper Methods ---

    async def set_fill_light(self, level: int) -> None:
        """Set fill light brightness (0-100)."""
        value = round(level * BRIGHTNESS_HA_MAX / BRIGHTNESS_DEVICE_MAX)
        await self._post("/peripheral/fill_light", data={"action": "set_bri", "idx": 0, "value": value})

    async def pause_job(self) -> None:
        """Pause the current job."""
        await self._get("/processing/pause")

    async def resume_job(self) -> None:
        """Resume the current job."""
        await self._get("/processing/resume")

    async def cancel_job(self) -> None:
        """Cancel the current job."""
        await self._get("/processing/stop")

    # --- HTTP Helpers ---

    async def _get(self, path: str, timeout: float = 5.0) -> str | None:
        url = f"{self._base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status == 200:
                        return await resp.text()
        except Exception as err:
            _LOGGER.debug("REST GET %s failed: %s", url, err)
        return None

    async def _get_json(self, path: str, timeout: float = 5.0) -> dict | None:
        url = f"{self._base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        # Some endpoints wrap in {"code": 0, "data": {...}}
                        if isinstance(data, dict) and "data" in data:
                            return data["data"]
                        return data
        except Exception as err:
            _LOGGER.debug("REST GET JSON %s failed: %s", url, err)
        return None

    async def _post(self, path: str, data: Any = None, timeout: float = 5.0) -> str | None:
        url = f"{self._base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=data,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        return await resp.text()
        except Exception as err:
            _LOGGER.debug("REST POST %s failed: %s", url, err)
        return None

    async def _post_json(self, path: str, data: Any = None, timeout: float = 5.0) -> dict | None:
        url = f"{self._base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=data,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json(content_type=None)
                        if isinstance(result, dict) and "data" in result:
                            return result["data"]
                        return result
        except Exception as err:
            _LOGGER.debug("REST POST JSON %s failed: %s", url, err)
        return None


def _map_rest_status(data: dict) -> int:
    """Map REST /cnc/status response to M222-compatible status codes."""
    mode = data.get("mode", "")
    sub_mode = data.get("subMode", "")
    status_map = {
        "idle": 3,
        "homing": 0,
        "running": 14,
        "pause": 15,
        "finish": 19,
        "alarm": 4,
    }
    return status_map.get(mode, status_map.get(sub_mode, -1))


def _map_rest_mode(mode: str) -> int:
    """Map REST /device/runningStatus mode string to M222-compatible status codes."""
    mode_map = {
        "IDLE": 3,
        "HOMING": 0,
        "PROCESSING": 14,
        "PAUSE": 15,
        "FINISHED": 19,
        "ALARM": 4,
        "UPGRADING": 16,
    }
    return mode_map.get(mode.upper(), -1)
