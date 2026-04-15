"""Select entities for xTool Laser integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .const import CMD_FLAME_ALARM, FlameAlarmSensitivity
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity

SENSITIVITY_OPTIONS = {
    "high": FlameAlarmSensitivity.HIGH,
    "low": FlameAlarmSensitivity.LOW,
}
SENSITIVITY_REVERSE = {v: k for k, v in SENSITIVITY_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool select entities."""
    coordinator = entry.runtime_data
    if coordinator.model.has_flame_alarm:
        async_add_entities([XtoolFlameAlarmSensitivity(coordinator)])


class XtoolFlameAlarmSensitivity(XtoolEntity, SelectEntity):
    """Select entity for flame alarm sensitivity level."""

    _attr_translation_key = "flame_alarm_sensitivity"
    _attr_icon = "mdi:fire-alert"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(SENSITIVITY_OPTIONS.keys())

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_flame_alarm_sensitivity"

    @property
    def current_option(self) -> str | None:
        """Return current sensitivity level."""
        if self.coordinator.data is None:
            return None
        alarm = self.coordinator.data.flame_alarm
        if alarm == FlameAlarmSensitivity.OFF:
            return None
        return SENSITIVITY_REVERSE.get(alarm)

    async def async_select_option(self, option: str) -> None:
        """Set the sensitivity level."""
        value = SENSITIVITY_OPTIONS[option]
        await self.coordinator.send_command(f"{CMD_FLAME_ALARM} A{value}")
        if self.coordinator.data:
            self.coordinator.data.flame_alarm = value
        self.async_write_ha_state()
