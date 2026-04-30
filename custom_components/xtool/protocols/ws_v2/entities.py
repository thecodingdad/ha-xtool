"""WS-V2 entities — minimum entity set for V2 firmware devices.

Initial scope: status sensor, cover, machine lock, last button event,
firmware update. Expansion (peripherals, gyro, water, cameras) follows
once a V2 device is available for live testing.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.update import UpdateEntity
from homeassistant.const import EntityCategory

from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity
from ...sensor import XtoolSensor, XtoolSensorEntityDescription
from ...update import XtoolFirmwareUpdate

_LOGGER = logging.getLogger(__name__)


# --- sensors -----------------------------------------------------------

WSV2_SENSOR_DESCRIPTIONS: tuple[XtoolSensorEntityDescription, ...] = (
    XtoolSensorEntityDescription(
        key="last_button_event",
        translation_key="last_button_event",
        icon="mdi:gesture-tap-button",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state, _: state.last_button_event or None,
    ),
    XtoolSensorEntityDescription(
        key="task_id",
        translation_key="task_id",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state, _: state.task_id or None,
    ),
    XtoolSensorEntityDescription(
        key="working_mode",
        translation_key="working_mode",
        icon="mdi:cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state, _: state.working_mode or None,
    ),
)


# --- binary sensors ----------------------------------------------------


class WSV2CoverSensor(XtoolEntity, BinarySensorEntity):
    """Cover open/closed (`/v1/peripheral/param?type=gap`)."""

    _attr_translation_key = "cover_open"
    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_cover_open"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.cover_open


class WSV2MachineLock(XtoolEntity, BinarySensorEntity):
    """Machine lock state (`/v1/peripheral/param?type=machine_lock`).

    Per the V2 push contract, OPEN means unlocked and CLOSE means locked.
    HA's LOCK device class wants ``True`` = unlocked.
    """

    _attr_translation_key = "machine_lock"
    _attr_device_class = BinarySensorDeviceClass.LOCK

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_machine_lock"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.machine_lock


class WSV2AirAssistConnected(XtoolEntity, BinarySensorEntity):
    """Air-Assist V2 BLE accessory present (`/v1/peripheral/param?type=airassistV2`)."""

    _attr_translation_key = "air_assist_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_air_assist_connected"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.air_assist_connected


# --- firmware update ----------------------------------------------------


class WSV2FirmwareUpdate(XtoolFirmwareUpdate):
    """V2 firmware update — same orchestrator, just a thin name marker."""


# --- builders -----------------------------------------------------------


def build_wsv2_sensors(coordinator: XtoolCoordinator) -> list[SensorEntity]:
    return [
        XtoolSensor(coordinator, description)
        for description in WSV2_SENSOR_DESCRIPTIONS
    ]


def build_wsv2_binary_sensors(coordinator: XtoolCoordinator) -> list[BinarySensorEntity]:
    entities: list[BinarySensorEntity] = []
    if coordinator.model.has_lid_sensor or coordinator.model.has_cover_sensor:
        entities.append(WSV2CoverSensor(coordinator))
    if coordinator.model.has_machine_lock:
        entities.append(WSV2MachineLock(coordinator))
    if coordinator.model.has_air_assist_state:
        entities.append(WSV2AirAssistConnected(coordinator))
    return entities


def build_wsv2_updates(coordinator: XtoolCoordinator) -> list[UpdateEntity]:
    if not coordinator.model.firmware_content_id:
        return []
    return [WSV2FirmwareUpdate(coordinator)]
