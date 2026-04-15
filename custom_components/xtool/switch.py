"""Switch entities for xTool Laser integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import XtoolConfigEntry
from .const import CMD_BEEPER, CMD_FLAME_ALARM, CMD_MOVE_STOP, CMD_SMOKING_FAN
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity
from .models import XtoolDeviceModel, XtoolDeviceState


@dataclass(frozen=True, kw_only=True)
class XtoolSwitchEntityDescription(SwitchEntityDescription):
    """Describes an xTool switch entity."""

    is_on_fn: Callable[[XtoolDeviceState], bool]
    turn_on_cmd: Callable[[XtoolDeviceState], str]
    turn_off_cmd: Callable[[XtoolDeviceState], str]
    update_state_fn: Callable[[XtoolDeviceState, bool], None]
    available_fn: Callable[[XtoolDeviceModel], bool] = lambda _: True


def _update_flame_alarm(state: XtoolDeviceState, on: bool) -> None:
    state.flame_alarm = 0 if on else 2


def _update_beeper(state: XtoolDeviceState, on: bool) -> None:
    state.beeper_enabled = on


def _update_move_stop(state: XtoolDeviceState, on: bool) -> None:
    state.move_stop_enabled = on


def _update_smoking_fan(state: XtoolDeviceState, on: bool) -> None:
    state.smoking_fan_on = on


SWITCH_DESCRIPTIONS: tuple[XtoolSwitchEntityDescription, ...] = (
    XtoolSwitchEntityDescription(
        key="flame_alarm",
        translation_key="flame_alarm",
        icon="mdi:fire-alert",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda state: state.flame_alarm != 2,
        turn_on_cmd=lambda state: f"{CMD_FLAME_ALARM} A0",  # High sensitivity by default
        turn_off_cmd=lambda _: f"{CMD_FLAME_ALARM} A2",
        update_state_fn=_update_flame_alarm,
        available_fn=lambda model: model.has_flame_alarm,
    ),
    XtoolSwitchEntityDescription(
        key="buzzer",
        translation_key="buzzer",
        icon="mdi:volume-high",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda state: state.beeper_enabled,
        turn_on_cmd=lambda _: f"{CMD_BEEPER} S1",
        turn_off_cmd=lambda _: f"{CMD_BEEPER} S0",
        update_state_fn=_update_beeper,
        available_fn=lambda model: model.has_beeper,
    ),
    XtoolSwitchEntityDescription(
        key="move_stop",
        translation_key="move_stop",
        icon="mdi:hand-back-left",
        is_on_fn=lambda state: state.move_stop_enabled,
        turn_on_cmd=lambda _: f"{CMD_MOVE_STOP} N1",
        turn_off_cmd=lambda _: f"{CMD_MOVE_STOP} N0",
        update_state_fn=_update_move_stop,
        available_fn=lambda model: model.has_move_stop,
    ),
    XtoolSwitchEntityDescription(
        key="smoking_fan",
        translation_key="smoking_fan",
        icon="mdi:fan",
        is_on_fn=lambda state: state.smoking_fan_on,
        turn_on_cmd=lambda state: f"{CMD_SMOKING_FAN} N1 D{state.smoking_fan_duration}",
        turn_off_cmd=lambda _: f"{CMD_SMOKING_FAN} N0",
        update_state_fn=_update_smoking_fan,
        available_fn=lambda model: model.has_smoking_fan,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool switch entities."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        XtoolSwitch(coordinator, description)
        for description in SWITCH_DESCRIPTIONS
        if description.available_fn(coordinator.model)
    ]
    if coordinator.power_switch_entity_id:
        entities.append(XtoolPowerSwitch(coordinator))
    async_add_entities(entities)


class XtoolSwitch(XtoolEntity, SwitchEntity):
    """Representation of an xTool switch."""

    entity_description: XtoolSwitchEntityDescription

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        description: XtoolSwitchEntityDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.serial_number}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.is_on_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        if self.coordinator.data is None:
            return
        cmd = self.entity_description.turn_on_cmd(self.coordinator.data)
        await self.coordinator.send_command(cmd)
        self.entity_description.update_state_fn(self.coordinator.data, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        if self.coordinator.data is None:
            return
        cmd = self.entity_description.turn_off_cmd(self.coordinator.data)
        await self.coordinator.send_command(cmd)
        self.entity_description.update_state_fn(self.coordinator.data, False)
        self.async_write_ha_state()


class XtoolPowerSwitch(XtoolEntity, SwitchEntity):
    """Proxy switch that controls the laser's power supply (e.g. a smart plug).

    Listens for state changes on the underlying switch entity to react immediately.
    """

    _attr_translation_key = "power_switch"
    _attr_icon = "mdi:power-plug"
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        """Initialize the power switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_power_switch"

    async def async_added_to_hass(self) -> None:
        """Register state change listener for the underlying switch."""
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
        """Handle state change of the underlying switch entity."""
        self.async_write_ha_state()
        # Trigger a coordinator refresh so all entities re-evaluate availability
        self.coordinator.async_set_updated_data(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Available when the underlying switch entity exists."""
        if not self.coordinator.power_switch_entity_id:
            return False
        state = self.hass.states.get(self.coordinator.power_switch_entity_id)
        return state is not None

    @property
    def is_on(self) -> bool | None:
        """Return True if the power switch is on."""
        if not self.coordinator.power_switch_entity_id:
            return None
        state = self.hass.states.get(self.coordinator.power_switch_entity_id)
        if state is None:
            return None
        return state.state == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the power supply."""
        await self.hass.services.async_call(
            "homeassistant",
            SERVICE_TURN_ON,
            {"entity_id": self.coordinator.power_switch_entity_id},
            blocking=True,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the power supply."""
        await self.hass.services.async_call(
            "homeassistant",
            SERVICE_TURN_OFF,
            {"entity_id": self.coordinator.power_switch_entity_id},
            blocking=True,
        )
