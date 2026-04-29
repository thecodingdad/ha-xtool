"""F1 V2 family coordinator — TLS WebSocket listener, no command channel."""

from __future__ import annotations

import logging
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING

from ...coordinator import XtoolCoordinator
from ..base import XtoolDeviceState

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.components.number import NumberEntity
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.update import UpdateEntity

_LOGGER = logging.getLogger(__name__)


class F1V2Coordinator(XtoolCoordinator):
    """Coordinator for the xTool F1 V2 (firmware 40.51+)."""

    async def _async_update_data(self) -> XtoolDeviceState:
        if self.data and self.data.available:
            state = dataclass_replace(self.data)
        else:
            state = XtoolDeviceState()

        try:
            if not self.protocol.connected:
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

        except Exception as err:
            _LOGGER.debug("Error polling xTool F1 V2: %s", err)
            state.available = False
            await self.protocol.disconnect()

        return state

    async def _fetch_device_info(self) -> None:
        try:
            info = await self.protocol.get_device_info()
            if info.serial_number and not self.serial_number:
                self.serial_number = info.serial_number
            if info.main_firmware:
                self.firmware_version = info.main_firmware
        except Exception as err:
            _LOGGER.debug("Failed to fetch F1 V2 device info: %s", err)

    # --- Entity builders ----------------------------------------------------

    def build_switches(self) -> list["SwitchEntity"]:
        from .entities import build_f1v2_switches
        return build_f1v2_switches(self)

    def build_numbers(self) -> list["NumberEntity"]:
        from .entities import build_f1v2_numbers
        return build_f1v2_numbers(self)

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        from .entities import build_f1v2_binary_sensors
        return build_f1v2_binary_sensors(self)

    def build_sensors(self) -> list["SensorEntity"]:
        from .entities import build_f1v2_sensors
        return build_f1v2_sensors(self)

    def build_updates(self) -> list["UpdateEntity"]:
        from .entities import build_f1v2_updates
        return build_f1v2_updates(self)
