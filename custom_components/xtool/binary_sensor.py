"""Binary sensor platform — generic cover sensor + coord.build_binary_sensors()."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .coordinator import XtoolCoordinator
from .entity import XtoolRestoringBinarySensor


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool binary sensor entities."""
    coordinator = entry.runtime_data
    coordinator.register_platform_add("binary_sensor", async_add_entities)
    entities: list[BinarySensorEntity] = []
    model = coordinator.model

    # Cover/lid sensor — shared between F1 V2 (push) and REST cover models (gap poll).
    if model.has_lid_sensor or model.has_cover_sensor:
        entities.append(XtoolCoverSensor(coordinator))

    entities.extend(coordinator.build_binary_sensors())
    entities.extend(coordinator.initial_accessory_entities("binary_sensor"))
    async_add_entities(entities)


class XtoolCoverSensor(XtoolRestoringBinarySensor, BinarySensorEntity):
    """Cover/lid open sensor (F1 V2 push + REST cover models like P2/P2S)."""

    _attr_translation_key = "cover_open"
    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_icon = "mdi:window-shutter-open"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("cover_open")

    @property
    def is_on(self) -> bool | None:
        live: bool | None
        if self.coordinator.data is None:
            live = None
        else:
            live = self.coordinator.data.cover_open
        return self._is_on_or_restored(live)
