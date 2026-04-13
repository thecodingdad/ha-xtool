"""Light entity for xTool Laser integration - Fill Light."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .const import BRIGHTNESS_DEVICE_MAX, BRIGHTNESS_HA_MAX, CMD_FILL_LIGHT
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool light entities."""
    coordinator = entry.runtime_data
    if coordinator.model.has_fill_light:
        async_add_entities([XtoolFillLight(coordinator)])


class XtoolFillLight(XtoolEntity, LightEntity):
    """Representation of the xTool fill light (work light)."""

    _attr_translation_key = "fill_light"
    _attr_icon = "mdi:lightbulb-fluorescent-tube"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        """Initialize the fill light."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_fill_light"

    @property
    def brightness(self) -> int | None:
        """Return brightness (0-255 scale for HA)."""
        if self.coordinator.data is None:
            return None
        # Device uses 0-100, HA uses 0-255
        device_brightness = max(
            self.coordinator.data.fill_light_a,
            self.coordinator.data.fill_light_b,
        )
        return round(device_brightness * BRIGHTNESS_HA_MAX / BRIGHTNESS_DEVICE_MAX)

    @property
    def is_on(self) -> bool | None:
        """Return True if the light is physically on.

        Uses M15 (light_active) for physical state. M13 stores the
        configured brightness but the light dims during standby/pause.
        """
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.light_active

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the fill light."""
        if ATTR_BRIGHTNESS in kwargs:
            # Convert HA brightness (0-255) to device brightness (0-100)
            level = round(kwargs[ATTR_BRIGHTNESS] * BRIGHTNESS_DEVICE_MAX / BRIGHTNESS_HA_MAX)
        else:
            # Default to 100% if no brightness specified
            level = BRIGHTNESS_DEVICE_MAX

        await self.coordinator.send_command(f"{CMD_FILL_LIGHT} A{level}B{level}")
        # Update local state immediately
        if self.coordinator.data:
            self.coordinator.data.fill_light_a = level
            self.coordinator.data.fill_light_b = level
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fill light."""
        await self.coordinator.send_command(f"{CMD_FILL_LIGHT} A0B0")
        if self.coordinator.data:
            self.coordinator.data.fill_light_a = 0
            self.coordinator.data.fill_light_b = 0
        self.async_write_ha_state()
