"""Number entities for xTool Laser integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .const import CMD_AIR_ASSIST_DELAY, CMD_SMOKING_FAN
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity
from .models import XtoolDeviceModel, XtoolDeviceState


@dataclass(frozen=True, kw_only=True)
class XtoolNumberEntityDescription(NumberEntityDescription):
    """Describes an xTool number entity."""

    value_fn: Callable[[XtoolDeviceState], float]
    set_cmd_fn: Callable[[float], str]
    update_state_fn: Callable[[XtoolDeviceState, float], None]
    available_fn: Callable[[XtoolDeviceModel], bool] = lambda _: True


NUMBER_DESCRIPTIONS: tuple[XtoolNumberEntityDescription, ...] = (
    XtoolNumberEntityDescription(
        key="air_assist_close_delay",
        translation_key="air_assist_close_delay",
        icon="mdi:timer-sand",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=600,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda state: state.air_assist_close_delay,
        set_cmd_fn=lambda val: f"{CMD_AIR_ASSIST_DELAY} T{int(val)}",
        update_state_fn=lambda state, val: setattr(state, "air_assist_close_delay", int(val)),
    ),
    XtoolNumberEntityDescription(
        key="smoking_fan_duration",
        translation_key="smoking_fan_duration",
        icon="mdi:fan-clock",
        entity_category=EntityCategory.CONFIG,
        native_min_value=1,
        native_max_value=600,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda state: state.smoking_fan_duration,
        set_cmd_fn=lambda val: f"{CMD_SMOKING_FAN} D{int(val)}",
        update_state_fn=lambda state, val: setattr(state, "smoking_fan_duration", int(val)),
        available_fn=lambda model: model.has_smoking_fan,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool number entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        XtoolNumber(coordinator, description)
        for description in NUMBER_DESCRIPTIONS
        if description.available_fn(coordinator.model)
    )


class XtoolNumber(XtoolEntity, NumberEntity):
    """Representation of an xTool number setting."""

    entity_description: XtoolNumberEntityDescription

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        description: XtoolNumberEntityDescription,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.serial_number}_{description.key}"

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        cmd = self.entity_description.set_cmd_fn(value)
        await self.coordinator.send_command(cmd)
        if self.coordinator.data:
            self.entity_description.update_state_fn(self.coordinator.data, value)
        self.async_write_ha_state()
