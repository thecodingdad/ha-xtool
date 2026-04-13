"""Base entity for xTool integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import XtoolCoordinator


class XtoolEntity(CoordinatorEntity[XtoolCoordinator]):
    """Base entity for xTool devices."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.serial_number)},
            name=self.coordinator.device_name,
            manufacturer="xTool",
            model=self.coordinator.model.name,
            serial_number=self.coordinator.serial_number,
            sw_version=self.coordinator.firmware_version,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        If a power switch is configured and off, entities are unavailable
        (the device is intentionally powered down).
        """
        if self.coordinator.power_switch_is_off:
            return False
        return super().available and (
            self.coordinator.data is not None and self.coordinator.data.available
        )
