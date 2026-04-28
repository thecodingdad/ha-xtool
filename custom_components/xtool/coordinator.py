"""DataUpdateCoordinator for xTool laser devices."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    M222_STATUS_MAP,
    XtoolStatus,
)
from .models import XtoolDeviceModel, XtoolDeviceState
from dataclasses import replace as dataclass_replace

from .protocol import DeviceInfo, LaserInfo, XtoolProtocol

_LOGGER = logging.getLogger(__name__)


class XtoolCoordinator(DataUpdateCoordinator[XtoolDeviceState]):
    """Coordinator to manage polling an xTool device."""

    def __init__(
        self,
        hass: HomeAssistant,
        protocol: XtoolProtocol,
        device_name: str,
        serial_number: str,
        firmware_version: str,
        model: XtoolDeviceModel,
        power_switch_entity_id: str | None = None,
        enable_firmware_updates: bool = False,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{serial_number}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.host = protocol.host
        self.protocol = protocol
        self.device_name = device_name
        self.serial_number = serial_number
        self.firmware_version = firmware_version
        self.model = model
        self.laser = LaserInfo()
        self.power_switch_entity_id = power_switch_entity_id
        self.enable_firmware_updates = enable_firmware_updates
        self._device_info_fetched = False
        self.laser_firmware: str = ""
        self.wifi_firmware: str = ""
        self.workspace_x: float = 0.0
        self.workspace_y: float = 0.0
        self.workspace_z: float = 0.0

    @property
    def power_switch_is_off(self) -> bool:
        """Return True if a power switch is configured and currently off."""
        if not self.power_switch_entity_id:
            return False
        state = self.hass.states.get(self.power_switch_entity_id)
        return state is not None and state.state == "off"

    @property
    def xcs_compatibility_mode(self) -> bool:
        """Return True if in XCS Compatibility Mode."""
        from .ws_protocol import WsMcodeProtocol

        if isinstance(self.protocol, WsMcodeProtocol):
            return self.protocol.xcs_compatibility_mode
        return False

    async def send_command(self, command: str) -> str:
        """Send a command to the device."""
        try:
            return await self.protocol.send_command(command)
        except Exception as err:
            _LOGGER.warning("Failed to send command %s: %s", command, err)
            return ""

    async def _async_update_data(self) -> XtoolDeviceState:
        """Poll the device for current state."""
        # Clone previous state so partial updates preserve old values
        # without mutating the live state object
        if self.data and self.data.available:
            state = dataclass_replace(self.data)
        else:
            state = XtoolDeviceState()

        try:
            # In XCS mode, don't try to reconnect WS — poll_state handles it
            if not self.xcs_compatibility_mode and not self.protocol.connected:
                await self.protocol.connect()

            # Fetch static device info once on first update
            if not self._device_info_fetched:
                await self._fetch_device_info()
                self._device_info_fetched = True

            # Delegate polling — only updates fields with successful responses
            await self.protocol.poll_state(state)

            state.available = True
            state.device_name = self.device_name
            state.serial_number = self.serial_number
            state.firmware_version = self.firmware_version
            state.laser = self.laser

            # Connection count (best-effort)
            try:
                state.connection_count = await self.protocol.get_connection_count()
            except Exception:
                pass

        except Exception as err:
            _LOGGER.debug("Error polling xTool device: %s", err)
            # In XCS Compatibility Mode or after recent kicks, keep cached state
            if self.xcs_compatibility_mode and self.data and self.data.available:
                _LOGGER.debug("XCS Compatibility Mode — keeping cached state")
                return self.data
            state.available = False
            await self.protocol.disconnect()

        return state

    async def _fetch_device_info(self) -> None:
        """Fetch static device info (serial, laser, firmware versions, workspace dims)."""
        try:
            info = await self.protocol.get_device_info()
            if info.serial_number and not self.serial_number:
                self.serial_number = info.serial_number
            if info.laser.power_watts:
                self.laser = info.laser
            # Authoritative main firmware comes from M99 (in M2003 JSON).
            # The HTTP /system?action=version endpoint on the S1 returns the
            # ESP32 firmware instead, so older config entries may have stored
            # the wrong version — override it here.
            if info.main_firmware:
                self.firmware_version = info.main_firmware
            self.laser_firmware = info.laser_firmware
            self.wifi_firmware = info.wifi_firmware
            if info.workspace_x:
                self.workspace_x = info.workspace_x
                self.workspace_y = info.workspace_y
                self.workspace_z = info.workspace_z
            # Cache for firmware update entity (per-board versions)
            self._device_info_cache = info
        except Exception as err:
            _LOGGER.debug("Failed to fetch device info: %s", err)

    async def async_shutdown(self) -> None:
        """Disconnect on shutdown."""
        await self.protocol.disconnect()
        await super().async_shutdown()

    def get_status(self) -> XtoolStatus:
        """Get the current device status as enum."""
        if self.data is None:
            return XtoolStatus.UNKNOWN
        return M222_STATUS_MAP.get(self.data.status_code, XtoolStatus.UNKNOWN)
