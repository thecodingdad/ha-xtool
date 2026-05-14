"""Base entity for xTool integration."""

from __future__ import annotations

import re

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import XtoolCoordinator

_MODEL_SLUG_RE = re.compile(r"[^a-z0-9]")


class XtoolEntity(CoordinatorEntity[XtoolCoordinator]):
    """Base entity for xTool devices.

    ``has_entity_name`` stays **on** so HA resolves the localized
    display name via ``translation_key`` (German UI keeps its
    "Auftragszeit" etc. labels). The serial-prefixed entity-id
    shape is enforced by a one-shot registry migration in
    :func:`__init__._migrate_entity_registry` because HA's
    ``suggested_object_id`` is only consulted on *fresh*
    registrations — without the migration, existing installs
    would keep their old non-serial-prefixed entity-ids.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)

    def _set_unique_id(self, key: str) -> None:
        """Stamp ``unique_id`` and a serial-prefixed ``suggested_object_id``.

        ``unique_id`` keeps the long-standing ``{serial}_{key}`` shape
        so existing registry entries continue to match. The
        ``suggested_object_id`` is consumed by HA on first
        registration — fresh entities land at
        ``<platform>.xtool_<model>_<serial>_<key>``. Existing
        registrations are rebased to the same shape by the
        integration's one-shot setup migration.
        """
        sid = self.coordinator.serial_number
        model_slug = _MODEL_SLUG_RE.sub(
            "", self.coordinator.model.model_id.lower()
        )
        self._attr_unique_id = f"{sid}_{key}"
        self._attr_suggested_object_id = (
            f"xtool_{model_slug}_{sid}_{key}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        connections: set[tuple[str, str]] = set()
        if self.coordinator.mac_address:
            connections.add((CONNECTION_NETWORK_MAC, self.coordinator.mac_address))
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.serial_number)},
            connections=connections,
            name=self.coordinator.device_name,
            manufacturer="xTool",
            model=self.coordinator.model.name,
            serial_number=self.coordinator.serial_number,
            sw_version=self.coordinator.firmware_version,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        If a power switch is configured and off, entities are unavailable
        (the device is intentionally powered down).
        """
        if self.coordinator.power_switch_is_off:
            return False
        return super().available and (
            self.coordinator.data is not None and self.coordinator.data.available
        )


class XtoolReadOnlyEntity(XtoolEntity):
    """Base for read-only entities (sensor, binary_sensor) that should
    remain available across device outages so the last-known value
    stays visible on dashboards. Controls (button / switch / number /
    select / camera) keep the stricter :class:`XtoolEntity.available`
    gate because actuating them requires a live device.
    """

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None
