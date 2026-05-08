"""Event platform — polymorphic dispatch via coord.build_events().

Per-family concrete event classes live in
``protocols/<family>/entities.py``. The base class + dispatcher signal
are defined here because every family's event class shares the same
"subscribe → filter by kind → ``_trigger_event``" wiring.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool event entities."""
    coordinator = entry.runtime_data
    entities: list[EventEntity] = list(coordinator.build_events())
    if entities:
        async_add_entities(entities)


class XtoolEvent(XtoolEntity, EventEntity):
    """Base class for every xTool event entity.

    Subclasses bind a ``_kind`` (``"button"`` / ``"job"`` / ``"error"`` /
    …) and a list of declared event types. The shared dispatcher
    subscription forwards every coordinator-emitted event whose
    ``kind`` matches into ``_trigger_event``.
    """

    _kind: str = ""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        event_types: tuple[str, ...],
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        self._attr_event_types = list(event_types)
        self._unsub_dispatcher: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass,
            f"xtool_event_{self.coordinator.serial_number}",
            self._handle_dispatched_event,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_dispatcher is not None:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_dispatched_event(
        self, kind: str, event_type: str, attributes: dict[str, Any] | None
    ) -> None:
        if kind != self._kind:
            return
        if event_type not in (self._attr_event_types or ()):
            _LOGGER.debug(
                "xTool %s event %r not in declared types %s — skipped",
                kind, event_type, self._attr_event_types,
            )
            return
        self._trigger_event(event_type, attributes or {})
        self.async_write_ha_state()
