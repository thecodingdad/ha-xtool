"""Fan platform — polymorphic dispatch via coord.build_fans()."""

from __future__ import annotations

from homeassistant.components.fan import FanEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool fan entities."""
    coordinator = entry.runtime_data
    coordinator.register_platform_add("fan", async_add_entities)
    entities: list[FanEntity] = list(coordinator.build_fans())
    entities.extend(coordinator.initial_accessory_entities("fan"))
    async_add_entities(entities)
