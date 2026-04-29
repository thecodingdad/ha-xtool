"""Light platform — polymorphic dispatch via coord.build_lights()."""

from __future__ import annotations

from homeassistant.components.light import LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool light entities."""
    coordinator = entry.runtime_data
    entities: list[LightEntity] = list(coordinator.build_lights())
    if entities:
        async_add_entities(entities)
