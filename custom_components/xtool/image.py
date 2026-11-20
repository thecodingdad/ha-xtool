"""Image platform — polymorphic dispatch via coord.build_images()."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool image entities."""
    coordinator = entry.runtime_data
    entities: list[ImageEntity] = list(coordinator.build_images())
    if entities:
        async_add_entities(entities)
