"""WS-V2 coordinator — owns the WSV2Protocol connection lifecycle."""

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
    from homeassistant.components.select import SelectEntity
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.update import UpdateEntity

_LOGGER = logging.getLogger(__name__)


class WSV2Coordinator(XtoolCoordinator):
    """Coordinator for the WS-V2 family (F1, F1U, F2 family, M1U, P2S, P3,
    MetalFab, Apparel Printer, F1 Lite, F1 Ultra V2 — anything that runs
    V2 firmware).

    Same lifecycle pattern as the legacy REST coordinator: open the
    protocol, periodically poll state into the shared
    ``XtoolDeviceState`` dataclass, hand state to entities through the
    standard HA `_async_update_data` hook.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Forward the model's ``firmware_machine_type`` so the V2 flash
        # handshake sends the right value.
        if hasattr(self.protocol, "set_machine_type"):
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
            _LOGGER.debug("WS-V2 poll failed: %s", err)
            state.available = False
            await self.protocol.disconnect()

        return state

    async def _fetch_device_info(self) -> None:
        try:
            info = await self.protocol.get_device_info()
            if info.serial_number and not self.serial_number:
                self.serial_number = info.serial_number
            if info.laser_power_watts:
                self.laser = info.laser
            if info.main_firmware:
                self.firmware_version = info.main_firmware
            if info.mac_address:
                self.mac_address = info.mac_address
        except Exception as err:
            _LOGGER.debug("WS-V2 device info fetch failed: %s", err)

    # --- Entity builders --------------------------------------------------

    def build_sensors(self) -> list["SensorEntity"]:
        from .entities import build_wsv2_sensors
        return build_wsv2_sensors(self)

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        from .entities import build_wsv2_binary_sensors
        return build_wsv2_binary_sensors(self)

    def build_updates(self) -> list["UpdateEntity"]:
        from .entities import build_wsv2_updates
        return build_wsv2_updates(self)

    # Stubs for platforms WS-V2 hasn't covered yet — keep the platform
    # dispatchers happy by returning empty lists.
    def build_switches(self) -> list["SwitchEntity"]:
        return []

    def build_numbers(self) -> list["NumberEntity"]:
        return []

    def build_buttons(self) -> list["ButtonEntity"]:
        return []

    def build_lights(self) -> list["LightEntity"]:
        return []

    def build_cameras(self) -> list["Camera"]:
        return []

    def build_selects(self) -> list["SelectEntity"]:
        return []
