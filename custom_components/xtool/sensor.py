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
from .entity import XtoolReadOnlyEntity, XtoolRestoringSensor
from .protocols import XtoolDeviceState


@dataclass(frozen=True, kw_only=True)
class XtoolSensorEntityDescription(SensorEntityDescription):
    """Generic description-driven sensor used by family entity factories."""

    value_fn: Callable[[XtoolDeviceState, XtoolCoordinator], str | int | float | None]
    # Unavailable while this returns False (the reading does not apply).
    available_fn: Callable[[XtoolDeviceState], bool] | None = None


class XtoolSensor(XtoolRestoringSensor, SensorEntity):
    """Description-driven sensor. Used by family entity factories."""

    entity_description: XtoolSensorEntityDescription

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        description: XtoolSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._set_unique_id(f"{description.key}")

    @property
    def available(self) -> bool:
        available_fn = self.entity_description.available_fn
        data = self.coordinator.data
        if available_fn is not None and data is not None and not available_fn(data):
            return False
        return super().available

    @property
    def native_value(self) -> str | int | float | None:
        live: str | int | float | None
        if self.coordinator.data is None:
            live = None
        else:
            live = self.entity_description.value_fn(
                self.coordinator.data, self.coordinator,
            )
        return self._value_or_restored(live)


class XtoolStatusSensor(XtoolReadOnlyEntity, SensorEntity):
    """Status sensor; always available.

    Off while the Power switch is off, or while a laser without a Power
    switch cannot be reached (it cannot be told apart from one that is
    off). Not responding while the Power switch is on but the laser does
    not answer after its start-up time.
    """

    _attr_translation_key = "status"
    _attr_icon = "mdi:list-status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [s.value for s in XtoolStatus]

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("status")
        # The last status the laser reported since it became reachable
        self._last_known_status: str | None = None

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str | None:
        if self.coordinator.power_switch_is_off:
            self._last_known_status = None
            return XtoolStatus.OFF
        if self.coordinator.data is None or not self.coordinator.data.available:
            self._last_known_status = None
            if self.coordinator.starting:
                # Just powered on: the laser is starting
                return XtoolStatus.INITIALIZING
            if self.coordinator.power_switch_entity_id:
                # Powered, but the laser does not answer
                return XtoolStatus.NOT_RESPONDING
            return XtoolStatus.OFF
        status = self.coordinator.data.status
        if status is not None:
            self._last_known_status = status.value
            return status.value
        if self._last_known_status is None and self.coordinator.starting:
            # The laser answers but has not reported a status yet
            return XtoolStatus.INITIALIZING
        return self._last_known_status


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool sensor entities."""
    coordinator = entry.runtime_data
    coordinator.register_platform_add("sensor", async_add_entities)
    entities: list[SensorEntity] = [XtoolStatusSensor(coordinator)]
    entities.extend(coordinator.build_sensors())
    entities.extend(coordinator.initial_accessory_entities("sensor"))
    async_add_entities(entities)
