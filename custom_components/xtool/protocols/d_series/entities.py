"""D-series entities — safety switches, thresholds, flame mode, diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.update import UpdateEntity
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfTime

from ...const import FlameAlarmSensitivity
from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity
from ...sensor import XtoolSensor, XtoolSensorEntityDescription
from ...update import XtoolFirmwareUpdate


# --- Safety switches (tilt / limit / moving stop) ---------------------------


class _DSeriesSafetySwitch(XtoolEntity, SwitchEntity):
    """Base class for D-series tilt/limit safety switches."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: XtoolCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key

    def _get_state(self) -> bool | None:
        return None

    async def _set_state(self, on: bool) -> None:
        ...

    @property
    def is_on(self) -> bool | None:
        return self._get_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_state(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_state(False)
        await self.coordinator.async_request_refresh()


class XtoolDSeriesTiltStop(_DSeriesSafetySwitch):
    _attr_icon = "mdi:angle-acute"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "tilt_stop")

    def _get_state(self) -> bool | None:
        d = self.coordinator.data
        return d.tilt_stop_enabled if d else None

    async def _set_state(self, on: bool) -> None:
        await self.coordinator.protocol.set_tilt_stop(on)


class XtoolDSeriesLimitStop(_DSeriesSafetySwitch):
    _attr_icon = "mdi:transit-skip"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "limit_stop")

    def _get_state(self) -> bool | None:
        d = self.coordinator.data
        return d.limit_stop_enabled if d else None

    async def _set_state(self, on: bool) -> None:
        await self.coordinator.protocol.set_limit_stop(on)


class XtoolDSeriesMovingStop(_DSeriesSafetySwitch):
    _attr_icon = "mdi:hand-back-left"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "move_stop")

    def _get_state(self) -> bool | None:
        d = self.coordinator.data
        return d.moving_stop_enabled if d else None

    async def _set_state(self, on: bool) -> None:
        await self.coordinator.protocol.set_moving_stop(on)


# --- Numbers ----------------------------------------------------------------


class XtoolDSeriesThreshold(XtoolEntity, NumberEntity):
    """D-series tilt/moving threshold (0-255)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 255
    _attr_native_step = 1

    def __init__(self, coordinator: XtoolCoordinator, kind: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._attr_unique_id = f"{coordinator.serial_number}_{kind}_threshold"
        self._attr_translation_key = f"{kind}_threshold"
        self._attr_icon = (
            "mdi:angle-acute" if kind == "tilt" else "mdi:axis-arrow"
        )

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        if d is None:
            return None
        return d.tilt_threshold if self._kind == "tilt" else d.moving_threshold

    async def async_set_native_value(self, value: float) -> None:
        n = int(value)
        if self._kind == "tilt":
            await self.coordinator.protocol.set_tilt_threshold(n)
            if self.coordinator.data:
                self.coordinator.data.tilt_threshold = n
        else:
            await self.coordinator.protocol.set_moving_threshold(n)
            if self.coordinator.data:
                self.coordinator.data.moving_threshold = n
        self.async_write_ha_state()


# --- Selects ----------------------------------------------------------------


_FLAME_ALARM_MODE_OPTIONS = ["mode_1", "mode_2", "mode_3", "mode_4"]
_FLAME_MODE_TO_INT = {opt: i + 1 for i, opt in enumerate(_FLAME_ALARM_MODE_OPTIONS)}
_FLAME_MODE_FROM_INT = {v: k for k, v in _FLAME_MODE_TO_INT.items()}

_SENSITIVITY_OPTIONS: dict[str, FlameAlarmSensitivity] = {
    "high": FlameAlarmSensitivity.HIGH,
    "low": FlameAlarmSensitivity.LOW,
    "off": FlameAlarmSensitivity.OFF,
}
_SENSITIVITY_REVERSE = {v: k for k, v in _SENSITIVITY_OPTIONS.items()}


class DSeriesFlameAlarmSensitivity(XtoolEntity, SelectEntity):
    """D-series flame alarm sensitivity — REST setter."""

    _attr_translation_key = "flame_alarm_sensitivity"
    _attr_icon = "mdi:fire-alert"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(_SENSITIVITY_OPTIONS.keys())

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_flame_alarm_sensitivity"

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return _SENSITIVITY_REVERSE.get(self.coordinator.data.flame_alarm)

    async def async_select_option(self, option: str) -> None:
        value = _SENSITIVITY_OPTIONS[option]
        await self.coordinator.protocol.set_flame_alarm_sensitivity(value)
        if self.coordinator.data:
            self.coordinator.data.flame_alarm = value
        self.async_write_ha_state()


class XtoolFlameAlarmMode(XtoolEntity, SelectEntity):
    """Flame alarm detection mode (D-series)."""

    _attr_translation_key = "flame_alarm_mode"
    _attr_icon = "mdi:fire-alert"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = _FLAME_ALARM_MODE_OPTIONS

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_flame_alarm_mode"

    @property
    def current_option(self) -> str | None:
        d = self.coordinator.data
        if d is None or not d.flame_alarm_mode:
            return None
        return _FLAME_MODE_FROM_INT.get(d.flame_alarm_mode)

    async def async_select_option(self, option: str) -> None:
        value = _FLAME_MODE_TO_INT.get(option)
        if value is None:
            return
        await self.coordinator.protocol.set_flame_alarm_mode(value)
        if self.coordinator.data:
            self.coordinator.data.flame_alarm_mode = value
        self.async_write_ha_state()


# --- Buttons ----------------------------------------------------------------


class XtoolQuitLightBurn(XtoolEntity, ButtonEntity):
    """D-series: leave LightBurn standby (M112 N0)."""

    _attr_translation_key = "quit_lightburn"
    _attr_icon = "mdi:exit-run"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_quit_lightburn"

    async def async_press(self) -> None:
        await self.coordinator.protocol.quit_lightburn_mode()


# --- Diagnostic sensors -----------------------------------------------------


class _DSeriesOriginOffset(XtoolEntity, SensorEntity):
    """Origin offset (D-series)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: XtoolCoordinator, axis: str) -> None:
        super().__init__(coordinator)
        self._axis = axis
        self._attr_unique_id = f"{coordinator.serial_number}_origin_offset_{axis}"
        self._attr_translation_key = f"origin_offset_{axis}"
        self._attr_icon = f"mdi:axis-{axis}-arrow"

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        if d is None:
            return None
        return d.origin_offset_x if self._axis == "x" else d.origin_offset_y


# --- D-series sensor descriptions ------------------------------------------

SENSOR_DESCRIPTIONS: tuple[XtoolSensorEntityDescription, ...] = (
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
    XtoolSensorEntityDescription(
        key="laser_power",
        translation_key="laser_power",
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="W",
        value_fn=lambda state, coord: coord.laser.power_watts or None,
    ),
    XtoolSensorEntityDescription(
        key="laser_module",
        translation_key="laser_module",
        icon="mdi:laser-pointer",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state, coord: coord.laser.type_name if coord.laser.power_watts else None,
    ),
    XtoolSensorEntityDescription(
        key="sd_card",
        translation_key="sd_card",
        icon="mdi:sd",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=["inserted", "not_inserted"],
        value_fn=lambda state, _: "inserted" if state.sd_card_present else "not_inserted",
    ),
)


# --- Builders ---------------------------------------------------------------


def build_d_series_switches(coordinator: XtoolCoordinator) -> list[SwitchEntity]:
    entities: list[SwitchEntity] = []
    model = coordinator.model
    if model.has_tilt_sensor:
        entities.append(XtoolDSeriesTiltStop(coordinator))
    if model.has_limit_switch:
        entities.append(XtoolDSeriesLimitStop(coordinator))
    if model.has_moving_sensor:
        entities.append(XtoolDSeriesMovingStop(coordinator))
    return entities


def build_d_series_numbers(coordinator: XtoolCoordinator) -> list[NumberEntity]:
    entities: list[NumberEntity] = []
    model = coordinator.model
    if model.has_tilt_sensor:
        entities.append(XtoolDSeriesThreshold(coordinator, "tilt"))
    if model.has_moving_sensor:
        entities.append(XtoolDSeriesThreshold(coordinator, "moving"))
    return entities


def build_d_series_selects(coordinator: XtoolCoordinator) -> list[SelectEntity]:
    entities: list[SelectEntity] = []
    if coordinator.model.has_flame_alarm:
        entities.append(DSeriesFlameAlarmSensitivity(coordinator))
    entities.append(XtoolFlameAlarmMode(coordinator))
    return entities


def build_d_series_buttons(coordinator: XtoolCoordinator) -> list[ButtonEntity]:
    return [XtoolQuitLightBurn(coordinator)]


def build_d_series_sensors(coordinator: XtoolCoordinator) -> list[SensorEntity]:
    entities: list[SensorEntity] = [
        XtoolSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.append(_DSeriesOriginOffset(coordinator, "x"))
    entities.append(_DSeriesOriginOffset(coordinator, "y"))
    return entities


# --- Firmware update -------------------------------------------------------


class DSeriesFirmwareUpdate(XtoolFirmwareUpdate):
    """D-series firmware update — single-package, base behaviour."""


def build_d_series_updates(coordinator: XtoolCoordinator) -> list[UpdateEntity]:
    if coordinator.model.firmware_content_id:
        return [DSeriesFirmwareUpdate(coordinator)]
    return []
