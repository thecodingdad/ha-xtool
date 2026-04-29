"""Camera platform — polymorphic dispatch via coord.build_cameras()."""

from __future__ import annotations

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool camera entities."""
    coordinator = entry.runtime_data
    entities: list[Camera] = list(coordinator.build_cameras())
    if entities:
        async_add_entities(entities)
