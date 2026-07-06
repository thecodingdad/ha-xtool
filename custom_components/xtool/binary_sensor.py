"""Binary sensor platform — thin dispatch to per-family builders."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool binary sensor entities."""
    coordinator = entry.runtime_data
    coordinator.register_platform_add("binary_sensor", async_add_entities)
    entities: list[BinarySensorEntity] = []
    entities.extend(coordinator.build_binary_sensors())
    entities.extend(coordinator.initial_accessory_entities("binary_sensor"))
    async_add_entities(entities)
