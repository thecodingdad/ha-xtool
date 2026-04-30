"""S1 family coordinator — AP2, XCS, multi-board firmware, workspace dims."""

from __future__ import annotations

import logging
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Any

from ...coordinator import XtoolCoordinator
from ..base import XtoolDeviceState

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.components.button import ButtonEntity
    from homeassistant.components.light import LightEntity
    from homeassistant.components.number import NumberEntity
    from homeassistant.components.select import SelectEntity
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.update import UpdateEntity

_LOGGER = logging.getLogger(__name__)


class S1Coordinator(XtoolCoordinator):
    """Coordinator for the xTool S1 (WebSocket M-code protocol)."""

    def __init__(self, *args: Any, has_ap2: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.has_ap2 = has_ap2
        # S1-only firmware/workspace fields populated by _fetch_device_info().
        self.laser_firmware: str = ""
        self.wifi_firmware: str = ""
        self.workspace_x: float = 0.0
        self.workspace_y: float = 0.0
        self.workspace_z: float = 0.0
        # Push AP2 toggle into the protocol so it can use it before first poll.
        self.protocol.set_ap2_enabled(has_ap2)
        # Forward poll intervals; protocol's defaults match const.py defaults.
        self.protocol.set_poll_intervals(
            ap2=self.ap2_poll_interval,
            stats=self.stats_poll_interval,
            dongle=self.dongle_poll_interval,
        )

    @property
    def xcs_compatibility_mode(self) -> bool:
        return self.protocol.xcs_compatibility_mode

    async def send_command(self, command: str) -> str:
        """Send an M-code command to the S1 protocol with safe error logging."""
        try:
            return await self.protocol.send_command(command)
        except Exception as err:
            _LOGGER.warning("Failed to send command %s: %s", command, err)
            return ""

    # --- Polling ------------------------------------------------------------

    async def _async_update_data(self) -> XtoolDeviceState:
        if self.data and self.data.available:
            state = dataclass_replace(self.data)
        else:
            state = XtoolDeviceState()

        try:
            # In XCS mode, don't try to reconnect WS — poll_state handles it.
            if not self.xcs_compatibility_mode and not self.protocol.connected:
                await self.protocol.connect()

            if not self._device_info_fetched:
                await self._fetch_device_info()
                self._device_info_fetched = True

            await self.protocol.poll_state(state)

            state.available = True
            state.device_name = self.device_name
            state.serial_number = self.serial_number
            state.firmware_version = self.firmware_version
            state.laser = self.laser

            try:
                state.connection_count = await self.protocol.get_connection_count()
            except Exception:
                pass

        except Exception as err:
            _LOGGER.debug("Error polling xTool S1: %s", err)
            # XCS Compatibility Mode — keep cached state on transient errors.
            if self.xcs_compatibility_mode and self.data and self.data.available:
                _LOGGER.debug("XCS Compatibility Mode — keeping cached state")
                return self.data
            state.available = False
            await self.protocol.disconnect()

        return state

    async def _fetch_device_info(self) -> None:
        try:
            info = await self.protocol.get_device_info()
            if info.serial_number and not self.serial_number:
                self.serial_number = info.serial_number
            if info.laser.power_watts:
                self.laser = info.laser
            # Authoritative main firmware comes from M99 (in M2003 JSON).
            if info.main_firmware:
                self.firmware_version = info.main_firmware
            self.laser_firmware = info.laser_firmware
            self.wifi_firmware = info.wifi_firmware
            if info.mac_address:
                self.mac_address = info.mac_address
            if info.workspace_x:
                self.workspace_x = info.workspace_x
                self.workspace_y = info.workspace_y
                self.workspace_z = info.workspace_z
            self._device_info_cache = info
        except Exception as err:
            _LOGGER.debug("Failed to fetch S1 device info: %s", err)

    # --- Entity builders ----------------------------------------------------

    def build_switches(self) -> list["SwitchEntity"]:
        from .entities import build_s1_switches
        return build_s1_switches(self)

    def build_numbers(self) -> list["NumberEntity"]:
        from .entities import build_s1_numbers
        return build_s1_numbers(self)

    def build_buttons(self) -> list["ButtonEntity"]:
        from .entities import build_s1_buttons
        return build_s1_buttons(self)

    def build_lights(self) -> list["LightEntity"]:
        from .entities import build_s1_lights
        return build_s1_lights(self)

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        from .entities import build_s1_binary_sensors
        return build_s1_binary_sensors(self)

    def build_sensors(self) -> list["SensorEntity"]:
        from .entities import build_s1_sensors
        return build_s1_sensors(self)

    def build_updates(self) -> list["UpdateEntity"]:
        from .entities import build_s1_updates
        return build_s1_updates(self)

    def build_selects(self) -> list["SelectEntity"]:
        from .entities import build_s1_selects
        return build_s1_selects(self)
