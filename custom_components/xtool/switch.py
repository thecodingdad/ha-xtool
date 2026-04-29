"""Switch platform — polymorphic dispatch via coord.build_switches()."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import XtoolConfigEntry
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool switch entities."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = list(coordinator.build_switches())
    if coordinator.power_switch_entity_id:
        entities.append(XtoolPowerSwitch(coordinator))
    async_add_entities(entities)


class XtoolPowerSwitch(XtoolEntity, SwitchEntity):
    """Proxy switch that controls the laser's power supply (e.g. a smart plug)."""

    _attr_translation_key = "power_switch"
    _attr_icon = "mdi:power-plug"
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_power_switch"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.power_switch_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self.coordinator.power_switch_entity_id],
                    self._on_underlying_switch_changed,
                )
            )

    @callback
    def _on_underlying_switch_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        self.async_write_ha_state()
        self.coordinator.async_set_updated_data(self.coordinator.data)

    @property
    def available(self) -> bool:
        if not self.coordinator.power_switch_entity_id:
            return False
        state = self.hass.states.get(self.coordinator.power_switch_entity_id)
        return state is not None

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.power_switch_entity_id:
            return None
        state = self.hass.states.get(self.coordinator.power_switch_entity_id)
        if state is None:
            return None
        return state.state == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hass.services.async_call(
            "homeassistant",
            SERVICE_TURN_ON,
            {"entity_id": self.coordinator.power_switch_entity_id},
            blocking=True,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hass.services.async_call(
            "homeassistant",
            SERVICE_TURN_OFF,
            {"entity_id": self.coordinator.power_switch_entity_id},
            blocking=True,
        )
