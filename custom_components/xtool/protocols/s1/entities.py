"""S1 entities — fill light, switches/numbers/buttons, AP2, accessories, alarm."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.components.update import UpdateEntity
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfTime

from ...const import (
    BRIGHTNESS_DEVICE_MAX,
    BRIGHTNESS_HA_MAX,
    FlameAlarmSensitivity,
)
from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity
from ...sensor import XtoolSensor, XtoolSensorEntityDescription
from ...update import XtoolFirmwareUpdate
from ..base import XtoolDeviceModel, XtoolDeviceState
from .protocol import (
    ACCESSORY_IDX_PURIFIER,
    ACCESSORY_NAMES,
    CMD_AIR_ASSIST_DELAY,
    CMD_BEEPER,
    CMD_CANCEL_JOB,
    CMD_FILL_LIGHT,
    CMD_FLAME_ALARM,
    CMD_HOME_ALL,
    CMD_HOME_XY,
    CMD_HOME_Z,
    CMD_MOVE_STOP,
    CMD_PAUSE_JOB,
    CMD_RESUME_JOB,
    CMD_SMOKING_FAN,
    RISER_BASE_NAMES,
)

# Used by the S1 lifetime-stat sensors (working_time / standby_time / tool_runtime).
SECONDS_PER_HOUR = 3600


# --- Generic description-driven helpers (S1 only uses them) ----------------


@dataclass(frozen=True, kw_only=True)
class XtoolSwitchEntityDescription(SwitchEntityDescription):
    is_on_fn: Callable[[XtoolDeviceState], bool]
    turn_on_cmd: Callable[[XtoolDeviceState], str]
    turn_off_cmd: Callable[[XtoolDeviceState], str]
    update_state_fn: Callable[[XtoolDeviceState, bool], None]
    available_fn: Callable[[XtoolDeviceModel], bool] = lambda _: True


@dataclass(frozen=True, kw_only=True)
class XtoolNumberEntityDescription(NumberEntityDescription):
    value_fn: Callable[[XtoolDeviceState], float]
    set_cmd_fn: Callable[[float], str]
    update_state_fn: Callable[[XtoolDeviceState, float], None]
    available_fn: Callable[[XtoolDeviceModel], bool] = lambda _: True


@dataclass(frozen=True, kw_only=True)
class XtoolButtonEntityDescription(ButtonEntityDescription):
    command: str


# --- Switch state mutators -------------------------------------------------


def _update_beeper(state: XtoolDeviceState, on: bool) -> None:
    state.beeper_enabled = on


def _update_move_stop(state: XtoolDeviceState, on: bool) -> None:
    state.move_stop_enabled = on


def _update_smoking_fan(state: XtoolDeviceState, on: bool) -> None:
    state.smoking_fan_on = on


SWITCH_DESCRIPTIONS: tuple[XtoolSwitchEntityDescription, ...] = (
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


NUMBER_DESCRIPTIONS: tuple[XtoolNumberEntityDescription, ...] = (
    XtoolNumberEntityDescription(
        key="air_assist_close_delay",
        translation_key="air_assist_close_delay",
        icon="mdi:weather-windy",
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


BUTTON_DESCRIPTIONS: tuple[XtoolButtonEntityDescription, ...] = (
    XtoolButtonEntityDescription(
        key="pause_job",
        translation_key="pause_job",
        icon="mdi:pause",
        command=CMD_PAUSE_JOB,
    ),
    XtoolButtonEntityDescription(
        key="resume_job",
        translation_key="resume_job",
        icon="mdi:play",
        command=CMD_RESUME_JOB,
    ),
    XtoolButtonEntityDescription(
        key="cancel_job",
        translation_key="cancel_job",
        icon="mdi:stop",
        command=CMD_CANCEL_JOB,
    ),
    XtoolButtonEntityDescription(
        key="home_all",
        translation_key="home_all",
        icon="mdi:axis-arrow",
        command=CMD_HOME_ALL,
    ),
    XtoolButtonEntityDescription(
        key="home_xy",
        translation_key="home_xy",
        icon="mdi:axis-x-y-arrow-lock",
        command=CMD_HOME_XY,
    ),
    XtoolButtonEntityDescription(
        key="home_z",
        translation_key="home_z",
        icon="mdi:axis-z-arrow",
        command=CMD_HOME_Z,
    ),
)


SENSOR_DESCRIPTIONS: tuple[XtoolSensorEntityDescription, ...] = (
    XtoolSensorEntityDescription(
        key="laser_position_x",
        translation_key="laser_position_x",
        icon="mdi:axis-x-arrow",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=2,
        value_fn=lambda state, _: state.position_x,
    ),
    XtoolSensorEntityDescription(
        key="laser_position_y",
        translation_key="laser_position_y",
        icon="mdi:axis-y-arrow",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=2,
        value_fn=lambda state, _: state.position_y,
    ),
    XtoolSensorEntityDescription(
        key="laser_position_z",
        translation_key="laser_position_z",
        icon="mdi:axis-z-arrow",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=2,
        value_fn=lambda state, _: state.position_z,
    ),
    XtoolSensorEntityDescription(
        key="fire_level",
        translation_key="fire_level",
        icon="mdi:fire",
        value_fn=lambda state, _: state.fire_level,
    ),
    XtoolSensorEntityDescription(
        key="air_assist_level",
        translation_key="air_assist_level",
        icon="mdi:weather-windy",
        value_fn=lambda state, _: state.air_assist_level,
    ),
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
        key="working_time",
        translation_key="working_time",
        icon="mdi:clock-outline",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda state, _: round(state.working_seconds / SECONDS_PER_HOUR, 1) if state.working_seconds is not None else None,
    ),
    XtoolSensorEntityDescription(
        key="session_count",
        translation_key="session_count",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda state, _: state.session_count,
    ),
    XtoolSensorEntityDescription(
        key="standby_time",
        translation_key="standby_time",
        icon="mdi:sleep",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda state, _: round(state.standby_seconds / SECONDS_PER_HOUR, 1) if state.standby_seconds is not None else None,
    ),
    XtoolSensorEntityDescription(
        key="tool_runtime",
        translation_key="tool_runtime",
        icon="mdi:laser-pointer",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda state, _: round(state.tool_runtime_seconds / SECONDS_PER_HOUR, 1) if state.tool_runtime_seconds is not None else None,
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
    XtoolSensorEntityDescription(
        key="workspace_size",
        translation_key="workspace_size",
        icon="mdi:ruler-square",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _, coord: (
            f"{coord.workspace_x:.0f} × {coord.workspace_y:.0f} × {coord.workspace_z:.0f} mm"
            if coord.workspace_x else None
        ),
    ),
    XtoolSensorEntityDescription(
        key="connection_count",
        translation_key="connection_count",
        icon="mdi:lan-connect",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state, _: state.connection_count,
    ),
)


# --- Concrete description-driven entities ----------------------------------


class XtoolSwitch(XtoolEntity, SwitchEntity):
    entity_description: XtoolSwitchEntityDescription

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        description: XtoolSwitchEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.serial_number}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.is_on_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.coordinator.data is None:
            return
        cmd = self.entity_description.turn_on_cmd(self.coordinator.data)
        await self.coordinator.send_command(cmd)
        self.entity_description.update_state_fn(self.coordinator.data, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.coordinator.data is None:
            return
        cmd = self.entity_description.turn_off_cmd(self.coordinator.data)
        await self.coordinator.send_command(cmd)
        self.entity_description.update_state_fn(self.coordinator.data, False)
        self.async_write_ha_state()


class XtoolNumber(XtoolEntity, NumberEntity):
    entity_description: XtoolNumberEntityDescription

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        description: XtoolNumberEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.serial_number}_{description.key}"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        cmd = self.entity_description.set_cmd_fn(value)
        await self.coordinator.send_command(cmd)
        if self.coordinator.data:
            self.entity_description.update_state_fn(self.coordinator.data, value)
        self.async_write_ha_state()


class XtoolButton(XtoolEntity, ButtonEntity):
    entity_description: XtoolButtonEntityDescription

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        description: XtoolButtonEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.serial_number}_{description.key}"

    async def async_press(self) -> None:
        await self.coordinator.send_command(self.entity_description.command)


# --- Fill light (S1 M13/M15) -----------------------------------------------


class XtoolFillLight(XtoolEntity, LightEntity):
    """Representation of the xTool fill light (work light)."""

    _attr_translation_key = "fill_light"
    _attr_icon = "mdi:track-light"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_fill_light"

    @property
    def brightness(self) -> int | None:
        if self.coordinator.data is None:
            return None
        device_brightness = max(
            self.coordinator.data.fill_light_a,
            self.coordinator.data.fill_light_b,
        )
        return round(device_brightness * BRIGHTNESS_HA_MAX / BRIGHTNESS_DEVICE_MAX)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.light_active

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            level = round(kwargs[ATTR_BRIGHTNESS] * BRIGHTNESS_DEVICE_MAX / BRIGHTNESS_HA_MAX)
        else:
            level = BRIGHTNESS_DEVICE_MAX
        await self.coordinator.send_command(f"{CMD_FILL_LIGHT} A{level}B{level}")
        if self.coordinator.data:
            self.coordinator.data.fill_light_a = level
            self.coordinator.data.fill_light_b = level
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.send_command(f"{CMD_FILL_LIGHT} A0B0")
        if self.coordinator.data:
            self.coordinator.data.fill_light_a = 0
            self.coordinator.data.fill_light_b = 0
        self.async_write_ha_state()


# --- Accessories / alarm / AP2 binary sensors ------------------------------


class XtoolAccessoriesSensor(XtoolEntity, BinarySensorEntity):
    """Indicates whether any accessories are attached (M1098)."""

    _attr_translation_key = "accessories"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:puzzle"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_accessories"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return len(self._get_attached()) > 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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


class XtoolAlarmSensor(XtoolEntity, BinarySensorEntity):
    """Generic alarm flag — true when device reports any active alarm (M340)."""

    _attr_translation_key = "alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alarm-light"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_alarm"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.alarm_present


class XtoolPurifierRunning(XtoolEntity, BinarySensorEntity):
    """AP2 purifier running state (M9039 push)."""

    _attr_translation_key = "purifier_running"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:air-purifier"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_purifier_running"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.purifier_on


class XtoolPurifierConnected(XtoolEntity, BinarySensorEntity):
    """AP2 connected (purifier slot in M1098 accessories array populated)."""

    _attr_translation_key = "purifier_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:air-purifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_purifier_connected"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        accs = self.coordinator.data.accessories_raw
        if ACCESSORY_IDX_PURIFIER >= len(accs):
            return False
        return bool(accs[ACCESSORY_IDX_PURIFIER])


# --- AP2 sensors (M9039 push) ----------------------------------------------


class _AP2Sensor(XtoolEntity, SensorEntity):
    """Common base for AP2 push-driven sensors."""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        icon: str,
        unit: str | None = None,
        category: EntityCategory | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        self._attr_icon = icon
        if unit is not None:
            self._attr_native_unit_of_measurement = unit
        if category is not None:
            self._attr_entity_category = category


class XtoolPurifierSpeed(_AP2Sensor):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "purifier_speed", "mdi:air-purifier")

    @property
    def native_value(self) -> int | None:
        d = self.coordinator.data
        return d.purifier_speed if d else None


class _FilterSensor(_AP2Sensor):
    _field: str

    def __init__(self, coordinator: XtoolCoordinator, key: str, field: str) -> None:
        super().__init__(coordinator, key, "mdi:air-filter", unit="%")
        self._field = field

    @property
    def native_value(self) -> int | None:
        d = self.coordinator.data
        if d is None:
            return None
        return getattr(d, self._field)


class _PurifierSensorRaw(_AP2Sensor):
    _field: str

    def __init__(self, coordinator: XtoolCoordinator, key: str, field: str) -> None:
        super().__init__(
            coordinator,
            key,
            "mdi:weather-dust",
            category=EntityCategory.DIAGNOSTIC,
        )
        self._field = field

    @property
    def native_value(self) -> int | None:
        d = self.coordinator.data
        if d is None:
            return None
        return getattr(d, self._field)


# --- Firmware update entity (S1 composite multi-board version) -------------


# Display labels for the three S1 boards in the composite version string.
_BOARD_LABELS = {
    "xcs-d2-0x20": "Main",
    "xcs-d2-0x21": "Laser",
    "xcs-d2-0x22": "WiFi",
}


def _short_version(raw: str) -> str:
    """Strip the 'V' prefix and any trailing build suffix for compact display."""
    if not raw:
        return "?"
    v = raw.lstrip("Vv")
    return v.split()[0] if v else "?"


class S1FirmwareUpdate(XtoolFirmwareUpdate):
    """S1 firmware update entity — renders Main/Laser/WiFi composite version."""

    @property
    def installed_version(self) -> str | None:
        return self._build_composite(self._current_board_versions())

    @property
    def latest_version(self) -> str | None:
        current = self._current_board_versions()
        new = dict(current)
        if self._update_info and self._update_info.board_versions:
            new.update(self._update_info.board_versions)
        return self._build_composite(new)

    def _current_board_versions(self) -> dict[str, str]:
        model = self.coordinator.model
        if not model.firmware_board_ids:
            return {}
        # Order: 0x20 main, 0x21 laser, 0x22 wifi (per S1 board map)
        sources = [
            self.coordinator.firmware_version,
            self.coordinator.laser_firmware,
            self.coordinator.wifi_firmware,
        ]
        return {
            board_id: src
            for board_id, src in zip(model.firmware_board_ids, sources)
            if src
        }

    def _build_composite(self, versions: dict[str, str]) -> str | None:
        if not versions:
            return None
        parts = [
            f"{_BOARD_LABELS.get(board_id, board_id)} {_short_version(ver)}"
            for board_id, ver in versions.items()
        ]
        return " / ".join(parts)


# --- Flame alarm sensitivity select (S1 sends M620) ------------------------

_SENSITIVITY_OPTIONS: dict[str, FlameAlarmSensitivity] = {
    "high": FlameAlarmSensitivity.HIGH,
    "low": FlameAlarmSensitivity.LOW,
    "off": FlameAlarmSensitivity.OFF,
}
_SENSITIVITY_REVERSE = {v: k for k, v in _SENSITIVITY_OPTIONS.items()}


class S1FlameAlarmSensitivity(XtoolEntity, SelectEntity):
    """S1 flame alarm sensitivity — sends M620 A<level>."""

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
        await self.coordinator.send_command(f"{CMD_FLAME_ALARM} A{value}")
        if self.coordinator.data:
            self.coordinator.data.flame_alarm = value
        self.async_write_ha_state()


# --- Builders ---------------------------------------------------------------


def build_s1_switches(coordinator: XtoolCoordinator) -> list[SwitchEntity]:
    model = coordinator.model
    return [
        XtoolSwitch(coordinator, description)
        for description in SWITCH_DESCRIPTIONS
        if description.available_fn(model)
    ]


def build_s1_numbers(coordinator: XtoolCoordinator) -> list[NumberEntity]:
    model = coordinator.model
    return [
        XtoolNumber(coordinator, description)
        for description in NUMBER_DESCRIPTIONS
        if description.available_fn(model)
    ]


def build_s1_buttons(coordinator: XtoolCoordinator) -> list[ButtonEntity]:
    return [XtoolButton(coordinator, description) for description in BUTTON_DESCRIPTIONS]


def build_s1_lights(coordinator: XtoolCoordinator) -> list[LightEntity]:
    if coordinator.model.has_fill_light:
        return [XtoolFillLight(coordinator)]
    return []


def build_s1_binary_sensors(coordinator: XtoolCoordinator) -> list[BinarySensorEntity]:
    entities: list[BinarySensorEntity] = [
        XtoolAccessoriesSensor(coordinator),
        XtoolAlarmSensor(coordinator),
    ]
    if coordinator.has_ap2:
        entities.append(XtoolPurifierRunning(coordinator))
        entities.append(XtoolPurifierConnected(coordinator))
    return entities


def build_s1_sensors(coordinator: XtoolCoordinator) -> list[SensorEntity]:
    entities: list[SensorEntity] = [
        XtoolSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    ]
    if coordinator.has_ap2:
        entities.extend([
            XtoolPurifierSpeed(coordinator),
            _FilterSensor(coordinator, "filter_pre", "filter_pre"),
            _FilterSensor(coordinator, "filter_medium", "filter_medium"),
            _FilterSensor(coordinator, "filter_carbon", "filter_carbon"),
            _FilterSensor(coordinator, "filter_dense_carbon", "filter_dense_carbon"),
            _FilterSensor(coordinator, "filter_hepa", "filter_hepa"),
            _PurifierSensorRaw(coordinator, "purifier_sensor_d", "purifier_sensor_d"),
            _PurifierSensorRaw(coordinator, "purifier_sensor_s", "purifier_sensor_s"),
        ])
    return entities


def build_s1_updates(coordinator: XtoolCoordinator) -> list[UpdateEntity]:
    if coordinator.model.firmware_content_id:
        return [S1FirmwareUpdate(coordinator)]
    return []


def build_s1_selects(coordinator: XtoolCoordinator) -> list[SelectEntity]:
    if coordinator.model.has_flame_alarm:
        return [S1FlameAlarmSensitivity(coordinator)]
    return []
