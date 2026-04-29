"""Button platform — polymorphic dispatch via coord.build_buttons()."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool button entities."""
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = list(coordinator.build_buttons())
    async_add_entities(entities)
