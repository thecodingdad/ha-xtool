"""REST family coordinator — pure HTTP polling."""

from __future__ import annotations

import logging
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING, Any

from ...coordinator import XtoolCoordinator
from ..base import XtoolDeviceState

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.components.button import ButtonEntity
    from homeassistant.components.camera import Camera
    from homeassistant.components.light import LightEntity
    from homeassistant.components.number import NumberEntity
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.update import UpdateEntity

_LOGGER = logging.getLogger(__name__)


class RestCoordinator(XtoolCoordinator):
    """Coordinator for F1, F1 Ultra, P1, P2, P2S, M1, M1 Ultra, GS005."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # REST flashing on P2/P2S/M1Ultra needs a machine_type query param.
        # The value is constant per model — set once at init so the protocol
        # carries it through every flash without needing a runtime hook.
        self.protocol.set_machine_type(self.model.firmware_machine_type)

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
            _LOGGER.debug("Error polling xTool REST device: %s", err)
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
            if info.main_firmware:
                self.firmware_version = info.main_firmware
            if info.mac_address:
                self.mac_address = info.mac_address
        except Exception as err:
            _LOGGER.debug("Failed to fetch REST device info: %s", err)

    # --- Entity builders ----------------------------------------------------

    def build_switches(self) -> list["SwitchEntity"]:
        from .entities import build_rest_switches
        return build_rest_switches(self)

    def build_numbers(self) -> list["NumberEntity"]:
        from .entities import build_rest_numbers
        return build_rest_numbers(self)

    def build_buttons(self) -> list["ButtonEntity"]:
        from .entities import build_rest_buttons
        return build_rest_buttons(self)

    def build_lights(self) -> list["LightEntity"]:
        from .entities import build_rest_lights
        return build_rest_lights(self)

    def build_cameras(self) -> list["Camera"]:
        from .entities import build_rest_cameras
        return build_rest_cameras(self)

    def build_sensors(self) -> list["SensorEntity"]:
        from .entities import build_rest_sensors
        return build_rest_sensors(self)

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        from .entities import build_rest_binary_sensors
        return build_rest_binary_sensors(self)

    def build_updates(self) -> list["UpdateEntity"]:
        from .entities import build_rest_updates
        return build_rest_updates(self)
