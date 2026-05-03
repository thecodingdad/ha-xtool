"""WS-V2 entities — full S1-scale coverage for V2-firmware devices.

Coverage is gated by the ``XtoolDeviceModel`` capability flags in
``ws_v2/models.py`` so per-model variation stays accurate. Action
paths use the helpers added in ``WSV2Protocol``:

- ``set_config(key, value)``        → /v1/device/configs PUT
- ``set_peripheral(type, action)``  → /v1/peripheral/control PUT
- ``set_mode(mode)``                → /v1/device/mode PUT
- ``camera_snap(type)``             → /v1/camera/snap GET (JPEG bytes)

See ``docs/PROTOCOL.md`` § "WS-V2 protocol" for the wire-level
contract and ``custom_components/xtool/protocols/rest/entities.py``
for the V1 REST equivalents whose patterns we mirror.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.camera import Camera
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.components.update import UpdateEntity
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.util import dt as dt_util

from ...const import BRIGHTNESS_DEVICE_MAX, BRIGHTNESS_HA_MAX
from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity
from ...sensor import XtoolSensor, XtoolSensorEntityDescription
from ...update import XtoolFirmwareUpdate

_LOGGER = logging.getLogger(__name__)

MIN_SNAPSHOT_INTERVAL = timedelta(seconds=30)


# --- Sensors -----------------------------------------------------------

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
    XtoolSensorEntityDescription(
        key="last_job_time",
        translation_key="last_job_time",
        icon="mdi:timer-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda state, _: state.last_job_time_seconds or None,
    ),
    XtoolSensorEntityDescription(
        key="task_time",
        translation_key="task_time",
        icon="mdi:timer",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda state, _: state.task_time or None,
    ),
    XtoolSensorEntityDescription(
        key="working_seconds",
        translation_key="working_time",
        icon="mdi:timer-cog",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda state, _: state.working_seconds,
    ),
    XtoolSensorEntityDescription(
        key="session_count",
        translation_key="session_count",
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda state, _: state.session_count,
    ),
    XtoolSensorEntityDescription(
        key="standby_seconds",
        translation_key="standby_time",
        icon="mdi:timer-sand",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda state, _: state.standby_seconds,
    ),
    XtoolSensorEntityDescription(
        key="tool_runtime_seconds",
        translation_key="tool_runtime",
        icon="mdi:tools",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda state, _: state.tool_runtime_seconds,
    ),
    XtoolSensorEntityDescription(
        key="print_tool_type",
        translation_key="print_tool_type",
        icon="mdi:cog",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state, _: state.print_tool_type or None,
    ),
)

# Sensors that are gated by capability flags
_GATED_SENSOR_DESCRIPTIONS: tuple[
    tuple[XtoolSensorEntityDescription, str], ...
] = (
    (
        XtoolSensorEntityDescription(
            key="position_x",
            translation_key="laser_position_x",
            icon="mdi:axis-x-arrow",
            native_unit_of_measurement=UnitOfLength.MILLIMETERS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda state, _: state.position_x,
        ),
        "has_laser_head_position",
    ),
    (
        XtoolSensorEntityDescription(
            key="position_y",
            translation_key="laser_position_y",
            icon="mdi:axis-y-arrow",
            native_unit_of_measurement=UnitOfLength.MILLIMETERS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda state, _: state.position_y,
        ),
        "has_laser_head_position",
    ),
    (
        XtoolSensorEntityDescription(
            key="position_z",
            translation_key="laser_position_z",
            icon="mdi:axis-z-arrow",
            native_unit_of_measurement=UnitOfLength.MILLIMETERS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda state, _: state.position_z,
        ),
        "has_z_axis",
    ),
    (
        XtoolSensorEntityDescription(
            key="last_distance_mm",
            translation_key="last_distance",
            icon="mdi:ruler",
            entity_category=EntityCategory.DIAGNOSTIC,
            native_unit_of_measurement=UnitOfLength.MILLIMETERS,
            value_fn=lambda state, _: state.last_distance_mm,
        ),
        "has_distance_measure",
    ),
    (
        XtoolSensorEntityDescription(
            key="water_temperature",
            translation_key="water_temperature",
            icon="mdi:water-thermometer",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda state, _: state.water_temperature,
        ),
        "has_water_cooling",
    ),
    (
        XtoolSensorEntityDescription(
            key="water_flow",
            translation_key="water_flow",
            icon="mdi:water-pump",
            device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
            native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda state, _: state.water_flow,
        ),
        "has_water_cooling",
    ),
    (
        XtoolSensorEntityDescription(
            key="z_temperature",
            translation_key="z_temperature",
            icon="mdi:thermometer",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda state, _: state.z_temperature,
        ),
        "has_z_temp",
    ),
    (
        XtoolSensorEntityDescription(
            key="workhead_id",
            translation_key="workhead_id",
            icon="mdi:tools",
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda state, _: state.workhead_id or None,
        ),
        "has_workhead_id",
    ),
    (
        XtoolSensorEntityDescription(
            key="gyro_x",
            translation_key="gyro_x",
            icon="mdi:axis-x-arrow",
            entity_category=EntityCategory.DIAGNOSTIC,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda state, _: state.gyro_x,
        ),
        "has_gyro",
    ),
    (
        XtoolSensorEntityDescription(
            key="gyro_y",
            translation_key="gyro_y",
            icon="mdi:axis-y-arrow",
            entity_category=EntityCategory.DIAGNOSTIC,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda state, _: state.gyro_y,
        ),
        "has_gyro",
    ),
    (
        XtoolSensorEntityDescription(
            key="gyro_z",
            translation_key="gyro_z",
            icon="mdi:axis-z-arrow",
            entity_category=EntityCategory.DIAGNOSTIC,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda state, _: state.gyro_z,
        ),
        "has_gyro",
    ),
)


# --- Binary sensors ----------------------------------------------------


class _WSV2BoolSensor(XtoolEntity, BinarySensorEntity):
    """Generic V2 binary sensor — reads a boolean from ``state.<attr>``."""

    _state_attr: str = ""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        device_class: BinarySensorDeviceClass | None = None,
        entity_category: EntityCategory | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        if device_class is not None:
            self._attr_device_class = device_class
        if entity_category is not None:
            self._attr_entity_category = entity_category

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return getattr(self.coordinator.data, self._state_attr, None)


def _bool_sensor_factory(
    state_attr: str,
    key: str,
    device_class: BinarySensorDeviceClass | None = None,
    entity_category: EntityCategory | None = None,
) -> type[_WSV2BoolSensor]:
    """Build a ``_WSV2BoolSensor`` subclass with the field bound."""
    cls = type(
        f"WSV2Bool_{key}",
        (_WSV2BoolSensor,),
        {"_state_attr": state_attr},
    )

    def _init(self: _WSV2BoolSensor, coordinator: XtoolCoordinator) -> None:
        _WSV2BoolSensor.__init__(
            self, coordinator, key, device_class, entity_category,
        )

    cls.__init__ = _init  # type: ignore[assignment]
    return cls


# --- Switches (configs PUT or peripheral control PUT) -----------------


class _WSV2ConfigSwitch(XtoolEntity, SwitchEntity):
    """Switch backed by a single key in ``/v1/device/configs``."""

    _attr_entity_category = EntityCategory.CONFIG
    _config_key: str = ""
    _state_attr: str = ""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        config_key: str,
        state_attr: str,
        icon: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        self._config_key = config_key
        self._state_attr = state_attr
        if icon is not None:
            self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        if d is None:
            return None
        return getattr(d, self._state_attr, None)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_config(self._config_key, True)
        if self.coordinator.data is not None:
            setattr(self.coordinator.data, self._state_attr, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_config(self._config_key, False)
        if self.coordinator.data is not None:
            setattr(self.coordinator.data, self._state_attr, False)
        self.async_write_ha_state()


class _WSV2PeripheralSwitch(XtoolEntity, SwitchEntity):
    """Switch backed by ``/v1/peripheral/control`` PUT (action on/off)."""

    _peripheral_type: str = ""
    _state_attr: str = ""
    _extra: dict[str, Any] = {}

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        peripheral_type: str,
        state_attr: str,
        icon: str | None = None,
        device_class: SwitchDeviceClass | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        self._peripheral_type = peripheral_type
        self._state_attr = state_attr
        if icon is not None:
            self._attr_icon = icon
        if device_class is not None:
            self._attr_device_class = device_class
        self._extra = extra or {}

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        if d is None:
            return None
        return getattr(d, self._state_attr, None)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_peripheral(
            self._peripheral_type, action="on", **self._extra,
        )
        if self.coordinator.data is not None:
            setattr(self.coordinator.data, self._state_attr, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_peripheral(
            self._peripheral_type, action="off", **self._extra,
        )
        if self.coordinator.data is not None:
            setattr(self.coordinator.data, self._state_attr, False)
        self.async_write_ha_state()


# --- Numbers -----------------------------------------------------------


class _WSV2ConfigNumber(XtoolEntity, NumberEntity):
    """Number backed by ``/v1/device/configs``."""

    _attr_entity_category = EntityCategory.CONFIG
    _config_key: str = ""
    _state_attr: str = ""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        config_key: str,
        state_attr: str,
        min_value: float,
        max_value: float,
        step: float = 1,
        unit: str | None = None,
        icon: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        self._config_key = config_key
        self._state_attr = state_attr
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step
        if unit is not None:
            self._attr_native_unit_of_measurement = unit
        if icon is not None:
            self._attr_icon = icon

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        if d is None:
            return None
        v = getattr(d, self._state_attr, None)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.protocol.set_config(self._config_key, int(value))
        if self.coordinator.data is not None:
            setattr(self.coordinator.data, self._state_attr, int(value))
        self.async_write_ha_state()


class WSV2DisplayBrightness(XtoolEntity, NumberEntity):
    """Display-screen brightness via ``/v1/peripheral/control``."""

    _attr_translation_key = "display_brightness"
    _attr_icon = "mdi:brightness-percent"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_display_brightness"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.display_brightness

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.protocol.set_peripheral(
            "digital_screen", action="set_brightness", value=int(value),
        )
        if self.coordinator.data is not None:
            self.coordinator.data.display_brightness = int(value)
        self.async_write_ha_state()


# --- Selects ------------------------------------------------------------


class WSV2FlameAlarmSelect(XtoolEntity, SelectEntity):
    """Flame-alarm sensitivity (configs PUT key=``flameAlarm``)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "flame_alarm_sensitivity"
    _attr_icon = "mdi:fire-alert"
    _attr_options = ["high", "low", "off"]

    _LABEL_TO_INT = {"high": 2, "low": 1, "off": 0}
    _INT_TO_LABEL = {v: k for k, v in _LABEL_TO_INT.items()}

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_flame_alarm_sensitivity"

    @property
    def current_option(self) -> str | None:
        d = self.coordinator.data
        if d is None:
            return None
        # State stores raw int; map to label.
        v = d.flame_alarm
        return self._INT_TO_LABEL.get(int(v) if v is not None else -1)

    async def async_select_option(self, option: str) -> None:
        raw = self._LABEL_TO_INT.get(option)
        if raw is None:
            return
        await self.coordinator.protocol.set_config("flameAlarm", raw)
        if self.coordinator.data is not None:
            self.coordinator.data.flame_alarm = raw
        self.async_write_ha_state()


class WSV2FlameLevelSelect(XtoolEntity, SelectEntity):
    """Flame level threshold (high/low)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "flame_level_hl"
    _attr_icon = "mdi:fire"
    _attr_options = ["high", "low"]
    _LABEL_TO_INT = {"high": 1, "low": 2}
    _INT_TO_LABEL = {v: k for k, v in _LABEL_TO_INT.items()}

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_flame_level"

    @property
    def current_option(self) -> str | None:
        d = self.coordinator.data
        if d is None or d.flame_level_hl is None:
            return None
        return self._INT_TO_LABEL.get(int(d.flame_level_hl))

    async def async_select_option(self, option: str) -> None:
        raw = self._LABEL_TO_INT.get(option)
        if raw is None:
            return
        await self.coordinator.protocol.set_config("flameLevelHLSelect", raw)
        if self.coordinator.data is not None:
            self.coordinator.data.flame_level_hl = raw
        self.async_write_ha_state()


class WSV2PurifierSpeedSelect(XtoolEntity, SelectEntity):
    """External-purifier speed via ``ext_purifier`` peripheral."""

    _attr_translation_key = "purifier_speed_select"
    _attr_icon = "mdi:air-purifier"
    _attr_options = ["off", "low", "medium", "high"]
    _LABEL_TO_INT = {"off": 0, "low": 1, "medium": 2, "high": 3}
    _INT_TO_LABEL = {v: k for k, v in _LABEL_TO_INT.items()}

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_purifier_speed"

    @property
    def current_option(self) -> str | None:
        d = self.coordinator.data
        if d is None:
            return None
        return self._INT_TO_LABEL.get(int(d.purifier_speed))

    async def async_select_option(self, option: str) -> None:
        raw = self._LABEL_TO_INT.get(option)
        if raw is None:
            return
        await self.coordinator.protocol.set_peripheral(
            "ext_purifier", action="set_speed", value=raw,
        )
        if self.coordinator.data is not None:
            self.coordinator.data.purifier_speed = raw
            self.coordinator.data.purifier_on = raw > 0
        self.async_write_ha_state()


# --- Light --------------------------------------------------------------


class WSV2FillLight(XtoolEntity, LightEntity):
    """Fill-light dimmable via ``/v1/peripheral/control?type=fill_light``."""

    _attr_translation_key = "fill_light"
    _attr_icon = "mdi:lightbulb"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_fill_light"

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        if d is None:
            return None
        return (d.fill_light_a or 0) > 0

    @property
    def brightness(self) -> int | None:
        d = self.coordinator.data
        if d is None:
            return None
        device_value = d.fill_light_a or 0
        # Scale device 0..BRIGHTNESS_DEVICE_MAX to HA 0..255.
        return int(
            device_value * BRIGHTNESS_HA_MAX / max(BRIGHTNESS_DEVICE_MAX, 1)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        ha_brightness = kwargs.get(ATTR_BRIGHTNESS, BRIGHTNESS_HA_MAX)
        device_brightness = int(
            ha_brightness * BRIGHTNESS_DEVICE_MAX / BRIGHTNESS_HA_MAX
        )
        await self.coordinator.protocol.set_peripheral(
            "fill_light", action="set_brightness", value=device_brightness,
        )
        if self.coordinator.data is not None:
            self.coordinator.data.fill_light_a = device_brightness
            self.coordinator.data.fill_light_b = device_brightness
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_peripheral(
            "fill_light", action="set_brightness", value=0,
        )
        if self.coordinator.data is not None:
            self.coordinator.data.fill_light_a = 0
            self.coordinator.data.fill_light_b = 0
        self.async_write_ha_state()


# --- Buttons ------------------------------------------------------------


class _WSV2Button(XtoolEntity, ButtonEntity):
    """Generic V2 button — overridden subclasses define ``_action()``."""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        icon: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        if icon is not None:
            self._attr_icon = icon

    async def _action(self) -> None:
        raise NotImplementedError

    async def async_press(self) -> None:
        await self._action()


class WSV2PauseJob(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "pause_job", "mdi:pause")

    async def _action(self) -> None:
        await self.coordinator.protocol.set_mode("P_PAUSE")


class WSV2ResumeJob(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "resume_job", "mdi:play")

    async def _action(self) -> None:
        await self.coordinator.protocol.set_mode("P_RESUME")


class WSV2CancelJob(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "cancel_job", "mdi:stop")

    async def _action(self) -> None:
        await self.coordinator.protocol.set_mode("P_IDLE")


class WSV2HomeAll(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "home_all", "mdi:home-floor-0")

    async def _action(self) -> None:
        await self.coordinator.protocol.set_peripheral(
            "laser_head", action="home_all",
        )


class WSV2HomeXY(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "home_xy", "mdi:home")

    async def _action(self) -> None:
        await self.coordinator.protocol.set_peripheral(
            "laser_head", action="home_xy",
        )


class WSV2HomeZ(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "home_z", "mdi:axis-z-arrow")

    async def _action(self) -> None:
        await self.coordinator.protocol.set_peripheral(
            "laser_head", action="home_z",
        )


class WSV2HomeLaser(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "home_laser_head", "mdi:crosshairs")

    async def _action(self) -> None:
        await self.coordinator.protocol.set_peripheral(
            "laser_head", action="move_to", x=0, y=0,
        )


class WSV2MeasureDistance(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "measure_distance", "mdi:ruler")

    async def _action(self) -> None:
        await self.coordinator.protocol.set_peripheral(
            "ir_measure_distance", action="measure",
        )


class WSV2Reboot(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "reboot", "mdi:restart")

    async def _action(self) -> None:
        await self.coordinator.protocol.set_config("reboot", 1)


class WSV2SyncTime(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "time_sync", "mdi:clock-time-eight")

    async def _action(self) -> None:
        # Send local time as Unix milliseconds.
        ms = int(dt_util.utcnow().timestamp() * 1000)
        await self.coordinator.protocol.set_config("setTime", ms)


# --- Cameras ------------------------------------------------------------


class _WSV2Camera(XtoolEntity, Camera):
    """Camera backed by ``/v1/camera/snap?type=<type>``.

    Snapshots are cached for ``MIN_SNAPSHOT_INTERVAL`` to keep the device
    CPU + WS bandwidth low. Live MJPEG via ``function=media_stream`` is
    a separate (parked) feature.
    """

    _camera_type: str = ""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        camera_type: str,
        icon: str | None = None,
    ) -> None:
        XtoolEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        self._camera_type = camera_type
        if icon is not None:
            self._attr_icon = icon
        self._last_snapshot: bytes | None = None
        self._last_snapshot_time = dt_util.utcnow() - MIN_SNAPSHOT_INTERVAL

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        now = dt_util.utcnow()
        if (
            self._last_snapshot is not None
            and now - self._last_snapshot_time < MIN_SNAPSHOT_INTERVAL
        ):
            return self._last_snapshot
        try:
            image = await self.coordinator.protocol.camera_snap(self._camera_type)
        except Exception as err:
            _LOGGER.debug("V2 camera %s snap failed: %s", self._camera_type, err)
            return self._last_snapshot
        if image:
            self._last_snapshot = image
            self._last_snapshot_time = now
        return self._last_snapshot


# --- Firmware update ----------------------------------------------------


class WSV2FirmwareUpdate(XtoolFirmwareUpdate):
    """V2 firmware update — same orchestrator, just a thin name marker."""


# --- Builders ------------------------------------------------------------


def build_wsv2_sensors(coordinator: XtoolCoordinator) -> list[SensorEntity]:
    entities: list[SensorEntity] = [
        XtoolSensor(coordinator, description)
        for description in WSV2_SENSOR_DESCRIPTIONS
    ]
    for description, flag in _GATED_SENSOR_DESCRIPTIONS:
        if getattr(coordinator.model, flag, False):
            entities.append(XtoolSensor(coordinator, description))
    return entities


def build_wsv2_binary_sensors(
    coordinator: XtoolCoordinator,
) -> list[BinarySensorEntity]:
    entities: list[BinarySensorEntity] = []
    model = coordinator.model

    if model.has_lid_sensor or model.has_cover_sensor:
        entities.append(
            _bool_sensor_factory(
                "cover_open", "cover_open",
                BinarySensorDeviceClass.DOOR,
            )(coordinator)
        )
    if model.has_drawer:
        entities.append(
            _bool_sensor_factory(
                "drawer_open", "drawer_open",
                BinarySensorDeviceClass.DOOR,
            )(coordinator)
        )
    if model.has_machine_lock:
        entities.append(
            _bool_sensor_factory(
                "machine_lock", "machine_lock",
                BinarySensorDeviceClass.LOCK,
            )(coordinator)
        )
    if model.has_air_assist_state:
        entities.append(
            _bool_sensor_factory(
                "air_assist_connected", "air_assist_connected",
                BinarySensorDeviceClass.CONNECTIVITY,
                EntityCategory.DIAGNOSTIC,
            )(coordinator)
        )

    # Always-on binary sensors (V2 baseline)
    entities.extend([
        _bool_sensor_factory(
            "cooling_fan_running", "cooling_fan_running",
            BinarySensorDeviceClass.RUNNING,
        )(coordinator),
        _bool_sensor_factory(
            "smoking_fan_running", "smoking_fan_running",
            BinarySensorDeviceClass.RUNNING,
        )(coordinator),
        _bool_sensor_factory(
            "alarm_present", "alarm",
            BinarySensorDeviceClass.PROBLEM,
        )(coordinator),
        _bool_sensor_factory(
            "flame_alarm_v2_enabled", "flame_alarm_enabled",
            None,
            EntityCategory.DIAGNOSTIC,
        )(coordinator),
        _bool_sensor_factory(
            "beep_enabled_v2", "beep_enabled",
            None,
            EntityCategory.DIAGNOSTIC,
        )(coordinator),
        _bool_sensor_factory(
            "gap_check_enabled", "gap_check_enabled",
            None,
            EntityCategory.DIAGNOSTIC,
        )(coordinator),
    ])

    if model.has_machine_lock:
        entities.append(
            _bool_sensor_factory(
                "machine_lock_check_enabled", "machine_lock_check_enabled",
                None,
                EntityCategory.DIAGNOSTIC,
            )(coordinator)
        )
    if model.has_uv_fire:
        entities.append(
            _bool_sensor_factory(
                "uv_fire_alarm", "uv_fire_alarm",
                BinarySensorDeviceClass.SMOKE,
            )(coordinator)
        )
    if model.has_water_cooling:
        entities.extend([
            _bool_sensor_factory(
                "water_pump_running", "water_pump_running",
                BinarySensorDeviceClass.RUNNING,
            )(coordinator),
            _bool_sensor_factory(
                "water_line_ok", "water_line_ok",
                BinarySensorDeviceClass.PROBLEM,
            )(coordinator),
        ])
    if model.has_cpu_fan:
        entities.append(
            _bool_sensor_factory(
                "cpu_fan_running", "cpu_fan_running",
                BinarySensorDeviceClass.RUNNING,
            )(coordinator)
        )
    return entities


def build_wsv2_switches(coordinator: XtoolCoordinator) -> list[SwitchEntity]:
    entities: list[SwitchEntity] = []
    model = coordinator.model

    # Config-backed toggles (always available on V2 firmware)
    entities.extend([
        _WSV2ConfigSwitch(
            coordinator, "beep_enable", "beepEnable", "beep_enabled_v2",
            "mdi:volume-high",
        ),
        _WSV2ConfigSwitch(
            coordinator, "flame_alarm_v2", "flameAlarm",
            "flame_alarm_v2_enabled", "mdi:fire-alert",
        ),
        _WSV2ConfigSwitch(
            coordinator, "gap_check", "gapCheck", "gap_check_enabled",
            "mdi:window-shutter-alert",
        ),
        _WSV2ConfigSwitch(
            coordinator, "filter_check", "filterCheck", "filter_check",
            "mdi:air-filter",
        ),
        _WSV2ConfigSwitch(
            coordinator, "purifier_check", "purifierCheck", "purifier_check",
            "mdi:air-purifier",
        ),
        _WSV2ConfigSwitch(
            coordinator, "purifier_continue", "purifierContinue",
            "purifier_continue", "mdi:autorenew",
        ),
    ])
    if model.has_machine_lock:
        entities.append(
            _WSV2ConfigSwitch(
                coordinator, "machine_lock_check", "machineLockCheck",
                "machine_lock_check_enabled", "mdi:lock-alert",
            )
        )
    if model.has_drawer:
        entities.append(
            _WSV2ConfigSwitch(
                coordinator, "drawer_check", "drawerCheck", "drawer_check",
                "mdi:archive-check",
            )
        )

    # Peripheral-control toggles
    cooling_fan = _WSV2PeripheralSwitch(
        coordinator, "cooling_fan", "cooling_fan", "cooling_fan_running",
        "mdi:fan", SwitchDeviceClass.SWITCH,
    )
    smoking_fan = _WSV2PeripheralSwitch(
        coordinator, "smoking_fan", "smoking_fan", "smoking_fan_running",
        "mdi:fan-chevron-up", SwitchDeviceClass.SWITCH,
    )
    entities.extend([cooling_fan, smoking_fan])

    if model.has_ir_led:
        entities.extend([
            _WSV2PeripheralSwitch(
                coordinator, "ir_led_close", "ir_led",
                "ir_led_close", "mdi:led-on", SwitchDeviceClass.SWITCH,
                extra={"index": "closeup"},
            ),
            _WSV2PeripheralSwitch(
                coordinator, "ir_led_global", "ir_led",
                "ir_led_global", "mdi:led-on", SwitchDeviceClass.SWITCH,
                extra={"index": "global"},
            ),
        ])
    if model.has_digital_lock:
        entities.append(
            _WSV2PeripheralSwitch(
                coordinator, "digital_lock", "digital_lock", "cover_locked",
                "mdi:lock", SwitchDeviceClass.SWITCH,
            )
        )
    return entities


def build_wsv2_numbers(coordinator: XtoolCoordinator) -> list[NumberEntity]:
    entities: list[NumberEntity] = [
        _WSV2ConfigNumber(
            coordinator, "air_assist_close_delay", "airAssistDelay",
            "air_assist_close_delay", 0, 600, 1,
            UnitOfTime.SECONDS, "mdi:fan-clock",
        ),
        _WSV2ConfigNumber(
            coordinator, "smoking_fan_duration", "smokingFanDelay",
            "smoking_fan_duration", 1, 600, 1,
            UnitOfTime.SECONDS, "mdi:fan-clock",
        ),
        _WSV2ConfigNumber(
            coordinator, "sleep_timeout", "sleepTimeout",
            "sleep_timeout", 0, 3600, 30,
            UnitOfTime.SECONDS, "mdi:timer-sand",
        ),
        _WSV2ConfigNumber(
            coordinator, "sleep_timeout_open_gap", "sleepTimeoutOpenGap",
            "sleep_timeout_open_gap", 0, 3600, 30,
            UnitOfTime.SECONDS, "mdi:timer-sand",
        ),
        _WSV2ConfigNumber(
            coordinator, "fire_level", "fireLevel",
            "fire_level", 0, 255, 1,
            None, "mdi:fire",
        ),
    ]
    model = coordinator.model
    if model.has_air_assist_state:
        entities.extend([
            _WSV2ConfigNumber(
                coordinator, "air_assist_gear_cut", "airassistCut",
                "air_assist_gear_cut", 0, 4, 1, None, "mdi:fan",
            ),
            _WSV2ConfigNumber(
                coordinator, "air_assist_gear_engrave", "airassistGrave",
                "air_assist_gear_grave", 0, 4, 1, None, "mdi:fan",
            ),
        ])
    if model.has_fill_light_rest:
        entities.append(
            _WSV2ConfigNumber(
                coordinator, "fill_light_auto_off", "fillLightAutoOff",
                "fill_light_auto_off", 0, 3600, 30,
                UnitOfTime.SECONDS, "mdi:lightbulb-off",
            )
        )
    if model.has_ir_led:
        entities.append(
            _WSV2ConfigNumber(
                coordinator, "ir_light_auto_off", "irLightAutoOff",
                "ir_light_auto_off", 0, 3600, 30,
                UnitOfTime.SECONDS, "mdi:led-off",
            )
        )
    if model.has_purifier_timeout:
        entities.append(
            _WSV2ConfigNumber(
                coordinator, "purifier_timeout", "purifierTimeout",
                "purifier_timeout", 0, 3600, 30,
                UnitOfTime.SECONDS, "mdi:air-purifier",
            )
        )
    if model.has_camera_exposure:
        entities.extend([
            _WSV2ConfigNumber(
                coordinator, "camera_exposure_overview", "exposureOverview",
                "camera_exposure_overview", 0, 255, 1, None, "mdi:camera-iris",
            ),
            _WSV2ConfigNumber(
                coordinator, "camera_exposure_closeup", "exposureCloseup",
                "camera_exposure_closeup", 0, 255, 1, None, "mdi:camera-iris",
            ),
        ])
    if model.has_display_screen:
        entities.append(WSV2DisplayBrightness(coordinator))
    return entities


def build_wsv2_selects(coordinator: XtoolCoordinator) -> list[SelectEntity]:
    return [
        WSV2FlameAlarmSelect(coordinator),
        WSV2FlameLevelSelect(coordinator),
        WSV2PurifierSpeedSelect(coordinator),
    ]


def build_wsv2_lights(coordinator: XtoolCoordinator) -> list[LightEntity]:
    if coordinator.model.has_fill_light_rest:
        return [WSV2FillLight(coordinator)]
    return []


def build_wsv2_buttons(coordinator: XtoolCoordinator) -> list[ButtonEntity]:
    entities: list[ButtonEntity] = [
        WSV2PauseJob(coordinator),
        WSV2ResumeJob(coordinator),
        WSV2CancelJob(coordinator),
        WSV2HomeXY(coordinator),
        WSV2HomeLaser(coordinator),
        WSV2Reboot(coordinator),
        WSV2SyncTime(coordinator),
    ]
    if coordinator.model.has_z_axis:
        entities.append(WSV2HomeAll(coordinator))
        entities.append(WSV2HomeZ(coordinator))
    if coordinator.model.has_distance_measure:
        entities.append(WSV2MeasureDistance(coordinator))
    return entities


def build_wsv2_cameras(coordinator: XtoolCoordinator) -> list[Camera]:
    if not coordinator.model.has_camera:
        return []
    cameras: list[Camera] = [
        _WSV2Camera(coordinator, "camera_overview", "overview", "mdi:camera"),
        _WSV2Camera(coordinator, "camera_closeup", "closeup", "mdi:camera-burst"),
    ]
    if coordinator.model.has_fire_record:
        cameras.append(
            _WSV2Camera(coordinator, "camera_fire_record", "fireRecord",
                        "mdi:fire-alert")
        )
    return cameras


def build_wsv2_updates(coordinator: XtoolCoordinator) -> list[UpdateEntity]:
    if not coordinator.model.firmware_content_id:
        return []
    return [WSV2FirmwareUpdate(coordinator)]
