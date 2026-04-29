"""Number platform — polymorphic dispatch via coord.build_numbers()."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool number entities."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = list(coordinator.build_numbers())
    async_add_entities(entities)
