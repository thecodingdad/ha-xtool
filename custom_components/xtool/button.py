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
    coordinator.register_platform_add("button", async_add_entities)
    entities: list[ButtonEntity] = list(coordinator.build_buttons())
    entities.extend(coordinator.initial_accessory_entities("button"))
    async_add_entities(entities)
