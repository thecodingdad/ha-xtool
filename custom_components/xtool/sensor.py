"""Sensor platform — generic helpers + per-family dispatch via coord.build_sensors()."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .const import XtoolStatus
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity
from .protocols import XtoolDeviceState


@dataclass(frozen=True, kw_only=True)
class XtoolSensorEntityDescription(SensorEntityDescription):
    """Generic description-driven sensor used by family entity factories."""

    value_fn: Callable[[XtoolDeviceState, XtoolCoordinator], str | int | float | None]


class XtoolSensor(XtoolEntity, SensorEntity):
    """Description-driven sensor. Used by family entity factories."""

    entity_description: XtoolSensorEntityDescription

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        description: XtoolSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.serial_number}_{description.key}"

    @property
    def native_value(self) -> str | int | float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data, self.coordinator)


class XtoolStatusSensor(XtoolEntity, SensorEntity):
    """Status sensor that shows 'off' when device is unreachable."""

    _attr_translation_key = "status"
    _attr_icon = "mdi:list-status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [s.value for s in XtoolStatus]

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_status"
        self._last_known_status: str = XtoolStatus.OFF

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if self.coordinator.power_switch_is_off:
            return XtoolStatus.OFF
        if self.coordinator.data is None or not self.coordinator.data.available:
            return XtoolStatus.OFF
        status = self.coordinator.data.status
        if status is not None:
            self._last_known_status = status.value
            return status.value
        return self._last_known_status


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool sensor entities."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [XtoolStatusSensor(coordinator)]
    entities.extend(coordinator.build_sensors())
    async_add_entities(entities)
