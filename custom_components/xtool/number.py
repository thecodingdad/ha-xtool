"""Number platform — polymorphic dispatch via coord.build_numbers()."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.components.number import NumberEntity
from homeassistant.const import SERVICE_TURN_OFF, EntityCategory, UnitOfTime
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from . import XtoolConfigEntry
from .const import XtoolStatus
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity

# The states Auto power-off counts: going between them keeps the count
AUTO_POWER_OFF_STATES = (XtoolStatus.IDLE, XtoolStatus.SLEEPING)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool number entities."""
    coordinator = entry.runtime_data
    coordinator.register_platform_add("number", async_add_entities)
    entities: list[NumberEntity] = list(coordinator.build_numbers())
    entities.extend(coordinator.initial_accessory_entities("number"))
    if coordinator.power_switch_entity_id:
        entities.append(XtoolAutoPowerOff(coordinator))
    async_add_entities(entities)


class XtoolAutoPowerOff(XtoolEntity, RestoreNumber):
    """Turn the linked Power switch off after the laser is idle or asleep.

    The laser has no power-off of its own, so this turns off the switch
    linked in the options (for example a smart plug). The count starts
    when the laser becomes Idle or Sleeping and keeps going while it goes
    between the two; any other state, or the laser going offline, stops
    it. 0 turns it off. The value is kept across restarts.
    """

    _attr_translation_key = "auto_power_off"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 1440
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("auto_power_off")
        self._attr_native_value = 0
        self._idle_since: datetime | None = None
        self._cancel_timer = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.coordinator.power_switch_entity_id], self._on_power_changed,
            )
        )
        self.async_on_remove(self._stop_timer)
        self._follow_status()

    @property
    def available(self) -> bool:
        # Settable while the laser is off: it only needs the Power switch
        return self.hass.states.get(self.coordinator.power_switch_entity_id) is not None

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._schedule()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._follow_status()
        super()._handle_coordinator_update()

    @callback
    def _on_power_changed(self, event: Event[EventStateChangedData]) -> None:
        self._follow_status()
        self.async_write_ha_state()

    def _counting(self) -> bool:
        data = self.coordinator.data
        return (
            not self.coordinator.power_switch_is_off
            and data is not None
            and data.available
            and data.status in AUTO_POWER_OFF_STATES
        )

    @callback
    def _follow_status(self) -> None:
        if not self._counting():
            self._idle_since = None
            self._stop_timer()
            return
        if self._idle_since is None:
            self._idle_since = dt_util.utcnow()
            self._schedule()

    @callback
    def _schedule(self) -> None:
        self._stop_timer()
        minutes = float(self._attr_native_value or 0)
        if minutes <= 0 or self._idle_since is None:
            return
        due = self._idle_since + timedelta(minutes=minutes)
        delay = max(0.0, (due - dt_util.utcnow()).total_seconds())
        self._cancel_timer = async_call_later(self.hass, delay, self._power_off)

    @callback
    def _stop_timer(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    async def _power_off(self, _now: datetime) -> None:
        self._cancel_timer = None
        if not self._counting():
            return
        await self.hass.services.async_call(
            "homeassistant",
            SERVICE_TURN_OFF,
            {"entity_id": self.coordinator.power_switch_entity_id},
            blocking=True,
        )
