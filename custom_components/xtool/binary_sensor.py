"""Binary sensor entities for xTool Laser integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .const import ACCESSORY_NAMES, RISER_BASE_NAMES
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool binary sensor entities."""
    coordinator = entry.runtime_data
    async_add_entities([XtoolAccessoriesSensor(coordinator)])


class XtoolAccessoriesSensor(XtoolEntity, BinarySensorEntity):
    """Binary sensor that indicates whether any accessories are attached."""

    _attr_translation_key = "accessories"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:puzzle"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        """Initialize the accessories sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_accessories"

    @property
    def is_on(self) -> bool | None:
        """Return True if any accessory is connected."""
        if self.coordinator.data is None:
            return None
        attached = self._get_attached()
        return len(attached) > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details about attached accessories."""
        if self.coordinator.data is None:
            return {}
        attached = self._get_attached()
        attrs: dict[str, Any] = {
            "accessories": [a["name"] for a in attached],
        }
        for accessory in attached:
            if accessory["firmware"]:
                attrs[f"{accessory['name']} firmware"] = accessory["firmware"]
        if self.coordinator.data.riser_base:
            riser_type = RISER_BASE_NAMES.get(
                self.coordinator.data.riser_base, RISER_BASE_NAMES[1]
            )
            attrs["riser_base"] = riser_type
        return attrs

    def _get_attached(self) -> list[dict[str, str]]:
        """Build list of attached accessories from M1098 array and M54."""
        if self.coordinator.data is None:
            return []
        attached: list[dict[str, str]] = []
        for idx, firmware in enumerate(self.coordinator.data.accessories_raw):
            if firmware and idx in ACCESSORY_NAMES:
                attached.append({
                    "name": ACCESSORY_NAMES[idx],
                    "firmware": firmware,
                })
        if self.coordinator.data.riser_base:
            riser_type = RISER_BASE_NAMES.get(
                self.coordinator.data.riser_base, RISER_BASE_NAMES[1]
            )
            attached.append({"name": riser_type, "firmware": ""})
        return attached
