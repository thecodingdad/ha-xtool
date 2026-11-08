"""Lock platform — polymorphic dispatch via coord.build_locks()."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool lock entities."""
    coordinator = entry.runtime_data
    entities: list[LockEntity] = list(coordinator.build_locks())
    async_add_entities(entities)
