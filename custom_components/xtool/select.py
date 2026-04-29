"""Select platform — polymorphic dispatch via coord.build_selects()."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool select entities."""
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = list(coordinator.build_selects())
    if entities:
        async_add_entities(entities)
