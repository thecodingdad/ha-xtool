"""F1 V2 entities — read-only push-driven toggles, sensors, machine lock."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.number import NumberEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.update import UpdateEntity
from homeassistant.const import EntityCategory, UnitOfTime

from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity
from ...sensor import XtoolSensor, XtoolSensorEntityDescription
from ...update import XtoolFirmwareUpdate


# --- Switches (read-only push-driven toggles) -------------------------------


class XtoolF1V2Toggle(XtoolEntity, SwitchEntity):
    """Read-only F1 V2 config toggle.

    F1 V2 has no documented command channel, so these toggles only mirror
    the device's current state from push events. async_turn_on/off are
    no-ops — the user must change them via the device or XCS.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True

    _FIELD_MAP = {
        "flame_alarm_v2": "flame_alarm_v2_enabled",
        "beep_v2": "beep_enabled_v2",
        "gap_check": "gap_check_enabled",
        "machine_lock_check": "machine_lock_check_enabled",
    }

    def __init__(self, coordinator: XtoolCoordinator, key: str, mdi_icon: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        self._attr_icon = mdi_icon
        self._field = self._FIELD_MAP[key]

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        return getattr(d, self._field, None) if d else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        ...

    async def async_turn_off(self, **kwargs: Any) -> None:
        ...


# --- Numbers ----------------------------------------------------------------


class XtoolPurifierTimeout(XtoolEntity, NumberEntity):
    """F1 V2 purifier auto-off timeout (read-only push, no setter)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 3600
    _attr_native_step = 30
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_icon = "mdi:timer"
    _attr_translation_key = "purifier_timeout"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_purifier_timeout"

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        return d.purifier_timeout if d else None

    async def async_set_native_value(self, value: float) -> None:
        # No documented write channel for F1 V2; expose as read-only.
        ...


# --- Binary sensors ---------------------------------------------------------


class XtoolMachineLockSensor(XtoolEntity, BinarySensorEntity):
    """F1 V2 machine lock sensor."""

    _attr_translation_key = "machine_lock"
    _attr_device_class = BinarySensorDeviceClass.LOCK
    _attr_icon = "mdi:lock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_machine_lock"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        # device_class=LOCK: True = unlocked
        if self.coordinator.data.machine_lock is None:
            return None
        return not self.coordinator.data.machine_lock


# --- Diagnostic sensors -----------------------------------------------------


class _F1V2DiagSensor(XtoolEntity, SensorEntity):
    """Base for F1 V2 push-only diagnostic sensors."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XtoolCoordinator, key: str, icon: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        self._attr_icon = icon


class XtoolLastJobTime(_F1V2DiagSensor):
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_device_class = SensorDeviceClass.DURATION

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "last_job_time", "mdi:timer-check")

    @property
    def native_value(self) -> int | None:
        d = self.coordinator.data
        return d.last_job_time_seconds if d else None


class XtoolWorkingMode(_F1V2DiagSensor):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "working_mode", "mdi:cog-play")

    @property
    def native_value(self) -> str | None:
        d = self.coordinator.data
        return (d.working_mode or None) if d else None


class XtoolLastButtonEvent(_F1V2DiagSensor):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "last_button_event", "mdi:gesture-tap-button")

    @property
    def native_value(self) -> str | None:
        d = self.coordinator.data
        return (d.last_button_event or None) if d else None


# --- F1 V2 sensor descriptions ---------------------------------------------

SENSOR_DESCRIPTIONS: tuple[XtoolSensorEntityDescription, ...] = (
    XtoolSensorEntityDescription(
        key="task_id",
        translation_key="task_id",
        icon="mdi:identifier",
        value_fn=lambda state, _: state.task_id or None,
    ),
    XtoolSensorEntityDescription(
        key="task_time",
        translation_key="task_time",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        value_fn=lambda state, _: state.task_time,
    ),
    XtoolSensorEntityDescription(
        key="ip_address",
        translation_key="ip_address",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _, coord: coord.host,
    ),
)


# --- Builders ---------------------------------------------------------------


def build_f1v2_switches(coordinator: XtoolCoordinator) -> list[SwitchEntity]:
    return [
        XtoolF1V2Toggle(coordinator, "flame_alarm_v2", "mdi:fire-alert"),
        XtoolF1V2Toggle(coordinator, "beep_v2", "mdi:volume-high"),
        XtoolF1V2Toggle(coordinator, "gap_check", "mdi:window-shutter-alert"),
        XtoolF1V2Toggle(coordinator, "machine_lock_check", "mdi:lock-alert"),
    ]


def build_f1v2_numbers(coordinator: XtoolCoordinator) -> list[NumberEntity]:
    if coordinator.model.has_purifier_timeout:
        return [XtoolPurifierTimeout(coordinator)]
    return []


def build_f1v2_binary_sensors(coordinator: XtoolCoordinator) -> list[BinarySensorEntity]:
    entities: list[BinarySensorEntity] = []
    if coordinator.model.has_machine_lock:
        entities.append(XtoolMachineLockSensor(coordinator))
    return entities


def build_f1v2_sensors(coordinator: XtoolCoordinator) -> list[SensorEntity]:
    entities: list[SensorEntity] = [
        XtoolSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.extend([
        XtoolLastJobTime(coordinator),
        XtoolWorkingMode(coordinator),
        XtoolLastButtonEvent(coordinator),
    ])
    return entities


# --- Firmware update -------------------------------------------------------


class F1V2FirmwareUpdate(XtoolFirmwareUpdate):
    """F1 V2 firmware update — display only (protocol has no flash channel)."""


def build_f1v2_updates(coordinator: XtoolCoordinator) -> list[UpdateEntity]:
    if coordinator.model.firmware_content_id:
        return [F1V2FirmwareUpdate(coordinator)]
    return []
