"""REST family entities — IR LEDs, digital lock, camera, fill light, etc."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import aiohttp

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
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.components.update import UpdateEntity
from homeassistant.const import (
    EntityCategory,
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
from .protocol import (
    CAMERA_EXPOSURE_MAX,
    CAMERA_EXPOSURE_MIN,
    IR_LED_INDEX_CLOSEUP,
    IR_LED_INDEX_GLOBAL,
    REST_CAMERA_PORT,
)

_LOGGER = logging.getLogger(__name__)

MIN_SNAPSHOT_INTERVAL = timedelta(seconds=30)


# --- Switches: IR LED + digital lock ----------------------------------------


class XtoolIRLED(XtoolEntity, SwitchEntity):
    """IR LED switch (close-up index 1, global index 2)."""

    def __init__(self, coordinator: XtoolCoordinator, kind: str) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self._index = IR_LED_INDEX_CLOSEUP if kind == "close" else IR_LED_INDEX_GLOBAL
        self._attr_unique_id = f"{coordinator.serial_number}_ir_led_{kind}"
        self._attr_translation_key = f"ir_led_{kind}"
        self._attr_icon = "mdi:led-on"

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        if d is None:
            return None
        return d.ir_led_close if self._kind == "close" else d.ir_led_global

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_ir_led(self._index, True)
        if self.coordinator.data:
            if self._kind == "close":
                self.coordinator.data.ir_led_close = True
            else:
                self.coordinator.data.ir_led_global = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_ir_led(self._index, False)
        if self.coordinator.data:
            if self._kind == "close":
                self.coordinator.data.ir_led_close = False
            else:
                self.coordinator.data.ir_led_global = False
        self.async_write_ha_state()


class XtoolDigitalLock(XtoolEntity, SwitchEntity):
    _attr_translation_key = "digital_lock"
    _attr_icon = "mdi:lock"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_digital_lock"

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        return d.cover_locked if d else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_digital_lock(True)
        if self.coordinator.data:
            self.coordinator.data.cover_locked = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_digital_lock(False)
        if self.coordinator.data:
            self.coordinator.data.cover_locked = False
        self.async_write_ha_state()


# --- Numbers: camera exposure ----------------------------------------------


class XtoolCameraExposure(XtoolEntity, NumberEntity):
    """Camera exposure for P2/P2S/F1 series."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = CAMERA_EXPOSURE_MIN
    _attr_native_max_value = CAMERA_EXPOSURE_MAX
    _attr_native_step = 1
    _attr_icon = "mdi:camera-iris"

    _STREAM_MAP = {"overview": 0, "closeup": 1}

    def __init__(self, coordinator: XtoolCoordinator, stream: str) -> None:
        super().__init__(coordinator)
        self._stream_name = stream
        self._stream_index = self._STREAM_MAP[stream]
        self._attr_unique_id = f"{coordinator.serial_number}_camera_exposure_{stream}"
        self._attr_translation_key = f"camera_exposure_{stream}"

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        if d is None:
            return None
        if self._stream_name == "overview":
            return d.camera_exposure_overview or None
        return d.camera_exposure_closeup or None

    async def async_set_native_value(self, value: float) -> None:
        n = int(value)
        await self.coordinator.protocol.set_camera_exposure(self._stream_index, n)
        if self.coordinator.data:
            if self._stream_name == "overview":
                self.coordinator.data.camera_exposure_overview = n
            else:
                self.coordinator.data.camera_exposure_closeup = n
        self.async_write_ha_state()


class XtoolAirAssistGear(XtoolEntity, NumberEntity):
    """Default Air-Assist gear for cut or engrave operations (M1 Ultra)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 4
    _attr_native_step = 1
    _attr_icon = "mdi:weather-windy"

    def __init__(self, coordinator: XtoolCoordinator, target: str) -> None:
        super().__init__(coordinator)
        self._target = target  # "cut" or "grave"
        self._attr_unique_id = f"{coordinator.serial_number}_air_assist_gear_{target}"
        self._attr_translation_key = f"air_assist_gear_{target}"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        d = self.coordinator.data
        return bool(d and d.air_assist_connected)

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        if d is None:
            return None
        return d.air_assist_gear_cut if self._target == "cut" else d.air_assist_gear_grave

    async def async_set_native_value(self, value: float) -> None:
        n = int(value)
        await self.coordinator.protocol.set_air_assist_gear(self._target, n)
        if self.coordinator.data:
            if self._target == "cut":
                self.coordinator.data.air_assist_gear_cut = n
            else:
                self.coordinator.data.air_assist_gear_grave = n
        self.async_write_ha_state()


# --- Buttons ---------------------------------------------------------------


class XtoolHomeLaserHead(XtoolEntity, ButtonEntity):
    """Move REST laser head back to (0, 0)."""

    _attr_translation_key = "home_laser_head"
    _attr_icon = "mdi:arrow-collapse-all"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_home_laser_head"

    async def async_press(self) -> None:
        await self.coordinator.protocol.move_laser_head(0.0, 0.0)


class XtoolMeasureDistance(XtoolEntity, ButtonEntity):
    """Trigger an IR distance measurement (P2/P2S)."""

    _attr_translation_key = "measure_distance"
    _attr_icon = "mdi:tape-measure"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_measure_distance"

    async def async_press(self) -> None:
        distance = await self.coordinator.protocol.measure_distance()
        if distance is not None and self.coordinator.data:
            self.coordinator.data.last_distance_mm = distance
            self.coordinator.async_set_updated_data(self.coordinator.data)


# --- Light (REST fill light) -----------------------------------------------


class XtoolRestFillLight(XtoolEntity, LightEntity):
    """Fill light driven via REST `/peripheral/fill_light`."""

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
        device = max(
            self.coordinator.data.fill_light_a,
            self.coordinator.data.fill_light_b,
        )
        return round(device * BRIGHTNESS_HA_MAX / BRIGHTNESS_DEVICE_MAX)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.fill_light_a > 0 or self.coordinator.data.fill_light_b > 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            ha_level = kwargs[ATTR_BRIGHTNESS]
            level = round(ha_level * BRIGHTNESS_DEVICE_MAX / BRIGHTNESS_HA_MAX)
        else:
            level = BRIGHTNESS_DEVICE_MAX
        await self.coordinator.protocol.set_fill_light(level)
        if self.coordinator.data:
            self.coordinator.data.fill_light_a = level
            self.coordinator.data.fill_light_b = level
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_fill_light(0)
        if self.coordinator.data:
            self.coordinator.data.fill_light_a = 0
            self.coordinator.data.fill_light_b = 0
        self.async_write_ha_state()


# --- Cameras ---------------------------------------------------------------


class XtoolCamera(XtoolEntity, Camera):
    """Representation of an xTool camera (P2/P2S overview or close-up)."""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        stream_index: int,
        key: str,
        translation_key: str,
    ) -> None:
        XtoolEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._stream_index = stream_index
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._last_image: bytes | None = None
        self._last_fetch = dt_util.utcnow() - MIN_SNAPSHOT_INTERVAL

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        now = dt_util.utcnow()
        if self._last_image and (now - self._last_fetch) < MIN_SNAPSHOT_INTERVAL:
            return self._last_image

        url = (
            f"http://{self.coordinator.host}:{REST_CAMERA_PORT}"
            f"/camera/snap?stream={self._stream_index}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        self._last_image = await resp.read()
                        self._last_fetch = now
                        return self._last_image
        except Exception as err:
            _LOGGER.debug("Camera snapshot failed: %s", err)
        return self._last_image


class XtoolFireRecordCamera(XtoolEntity, Camera):
    """F1 Ultra: snapshot of the most recent flame detection event."""

    _attr_translation_key = "camera_fire_record"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        XtoolEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._attr_unique_id = f"{coordinator.serial_number}_camera_fire_record"
        self._last_image: bytes | None = None
        self._last_fetch = dt_util.utcnow() - MIN_SNAPSHOT_INTERVAL

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        now = dt_util.utcnow()
        if self._last_image and (now - self._last_fetch) < MIN_SNAPSHOT_INTERVAL:
            return self._last_image
        data = await self.coordinator.protocol.get_fire_record()
        if data:
            self._last_image = data
            self._last_fetch = now
        return self._last_image


# --- Sensor (REST diagnostic) ----------------------------------------------


class XtoolLastDistance(XtoolEntity, SensorEntity):
    """Last IR distance measurement (P2/P2S)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_suggested_display_precision = 1
    _attr_translation_key = "last_distance"
    _attr_icon = "mdi:tape-measure"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_last_distance"

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        return d.last_distance_mm if d else None


# --- REST sensor descriptions ----------------------------------------------

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
)


# --- Builders ---------------------------------------------------------------


_REST_CAMERAS = (
    (0, "camera_overview", "camera_overview"),
    (1, "camera_closeup", "camera_closeup"),
)


def build_rest_switches(coordinator: XtoolCoordinator) -> list[SwitchEntity]:
    entities: list[SwitchEntity] = []
    model = coordinator.model
    if model.has_ir_led:
        entities.append(XtoolIRLED(coordinator, "close"))
        entities.append(XtoolIRLED(coordinator, "global"))
    if model.has_digital_lock:
        entities.append(XtoolDigitalLock(coordinator))
    # Universal REST toggles (each gates itself on data presence via available)
    entities.extend([
        XtoolBeepEnable(coordinator),
        XtoolFilterCheck(coordinator),
        XtoolPurifierCheck(coordinator),
        XtoolPurifierContinue(coordinator),
        XtoolCoolingFan(coordinator),
        XtoolSmokingFanRest(coordinator),
    ])
    if model.has_drawer:
        entities.append(XtoolDrawerCheck(coordinator))
    return entities


def build_rest_numbers(coordinator: XtoolCoordinator) -> list[NumberEntity]:
    entities: list[NumberEntity] = []
    if coordinator.model.has_camera_exposure:
        entities.append(XtoolCameraExposure(coordinator, "overview"))
        entities.append(XtoolCameraExposure(coordinator, "closeup"))
    if coordinator.model.has_air_assist_state:
        entities.append(XtoolAirAssistGear(coordinator, "cut"))
        entities.append(XtoolAirAssistGear(coordinator, "grave"))
    entities.extend([
        _sleep_timeout(coordinator),
        _sleep_timeout_open_gap(coordinator),
        _fill_light_auto_off(coordinator),
        _ir_light_auto_off(coordinator),
    ])
    if coordinator.model.has_display_screen:
        entities.append(XtoolDisplayBrightness(coordinator))
    return entities


def build_rest_buttons(coordinator: XtoolCoordinator) -> list[ButtonEntity]:
    entities: list[ButtonEntity] = []
    model = coordinator.model
    if model.has_laser_head_position:
        entities.append(XtoolHomeLaserHead(coordinator))
    if model.has_distance_measure:
        entities.append(XtoolMeasureDistance(coordinator))
    entities.append(XtoolReboot(coordinator))
    if model.has_water_cooling:
        entities.append(XtoolTimeSync(coordinator))
    return entities


def build_rest_selects(coordinator: XtoolCoordinator) -> list[SelectEntity]:
    entities: list[SelectEntity] = [
        XtoolPurifierSpeed(coordinator),
        XtoolFlameLevelHL(coordinator),
    ]
    return entities


def build_rest_lights(coordinator: XtoolCoordinator) -> list[LightEntity]:
    if coordinator.model.has_fill_light_rest:
        return [XtoolRestFillLight(coordinator)]
    return []


def build_rest_cameras(coordinator: XtoolCoordinator) -> list[Camera]:
    entities: list[Camera] = []
    model = coordinator.model
    if model.has_camera:
        entities.extend(
            XtoolCamera(coordinator, idx, key, t_key)
            for idx, key, t_key in _REST_CAMERAS
        )
    if model.has_fire_record:
        entities.append(XtoolFireRecordCamera(coordinator))
    return entities


def build_rest_sensors(coordinator: XtoolCoordinator) -> list[SensorEntity]:
    entities: list[SensorEntity] = [
        XtoolSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    model = coordinator.model
    if model.has_distance_measure:
        entities.append(XtoolLastDistance(coordinator))
    # Universal diagnostic sensors
    entities.extend([
        _RestStateSensor(coordinator, "last_button_event", "mdi:gesture-tap-button", "last_button_event"),
        _RestStateSensor(coordinator, "working_mode", "mdi:cog-play", "working_mode"),
        _RestStateSensor(coordinator, "print_tool_type", "mdi:tools", "print_tool_type"),
        _RestStateSensor(coordinator, "hardware_type", "mdi:chip", "hardware_type"),
    ])
    if model.has_water_cooling:
        entities.append(_RestTempSensor(coordinator, "water_temperature", "mdi:thermometer-water", "water_temperature"))
        entities.append(_RestFlowSensor(coordinator, "water_flow", "mdi:waves", "water_flow"))
    if model.has_z_temp:
        entities.append(_RestTempSensor(coordinator, "z_temperature", "mdi:thermometer", "z_temperature"))
    if model.has_workhead_id:
        entities.append(_RestStateSensor(coordinator, "workhead_id", "mdi:tools", "workhead_id"))
        entities.append(_RestNumericSensor(
            coordinator, "workhead_z_height", "mdi:axis-z-arrow",
            "workhead_z_height", unit=UnitOfLength.MILLIMETERS, precision=2,
        ))
    if model.has_gyro:
        for axis in ("x", "y", "z"):
            entities.append(_RestNumericSensor(
                coordinator, f"gyro_{axis}", f"mdi:axis-{axis}-arrow",
                f"gyro_{axis}", precision=2,
            ))
    return entities


# --- Binary sensors --------------------------------------------------------


class XtoolAirAssistConnected(XtoolEntity, BinarySensorEntity):
    """Air-Assist V2 (M1 Ultra accessory) connect state."""

    _attr_translation_key = "air_assist_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:weather-windy"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_air_assist_connected"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.air_assist_connected


def build_rest_binary_sensors(coordinator: XtoolCoordinator) -> list[BinarySensorEntity]:
    entities: list[BinarySensorEntity] = []
    model = coordinator.model
    if model.has_air_assist_state:
        entities.append(XtoolAirAssistConnected(coordinator))
    # Universal push-state binaries
    entities.extend([
        _RestPushBinary(coordinator, "cooling_fan_running", "mdi:fan",
                        "cooling_fan_running", BinarySensorDeviceClass.RUNNING),
        _RestPushBinary(coordinator, "smoking_fan_running", "mdi:fan",
                        "smoking_fan_running", BinarySensorDeviceClass.RUNNING),
    ])
    if model.has_drawer:
        entities.append(_RestPushBinary(coordinator, "drawer_open", "mdi:archive-arrow-up",
                                        "drawer_open", BinarySensorDeviceClass.OPENING))
    if model.has_cpu_fan:
        entities.append(_RestPushBinary(coordinator, "cpu_fan_running", "mdi:fan",
                                        "cpu_fan_running", BinarySensorDeviceClass.RUNNING))
    if model.has_uv_fire:
        entities.append(_RestPushBinary(coordinator, "uv_fire_alarm", "mdi:fire-alert",
                                        "uv_fire_alarm", BinarySensorDeviceClass.PROBLEM))
    if model.has_water_cooling:
        entities.append(_RestPushBinary(coordinator, "water_pump_running", "mdi:pump",
                                        "water_pump_running", BinarySensorDeviceClass.RUNNING))
        entities.append(_RestPushBinary(coordinator, "water_line_ok", "mdi:waves",
                                        "water_line_ok", BinarySensorDeviceClass.PROBLEM))
    return entities


# --- Generic REST switches (universal endpoints) ---------------------------


class _RestToggle(XtoolEntity, SwitchEntity):
    """Base for REST /set*/get* boolean toggles."""

    _attr_entity_category = EntityCategory.CONFIG
    _state_attr: str = ""
    _setter: str = ""

    def __init__(self, coordinator: XtoolCoordinator, key: str, icon: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        d = self.coordinator.data
        return bool(d and getattr(d, self._state_attr) is not None)

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        return getattr(d, self._state_attr) if d else None

    async def _call(self, on: bool) -> None:
        await getattr(self.coordinator.protocol, self._setter)(on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._call(True)
        if self.coordinator.data:
            setattr(self.coordinator.data, self._state_attr, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._call(False)
        if self.coordinator.data:
            setattr(self.coordinator.data, self._state_attr, False)
        self.async_write_ha_state()


class XtoolBeepEnable(_RestToggle):
    _state_attr = "beep_enabled"
    _setter = "set_beep_enabled"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "beep_enable", "mdi:volume-high")


class XtoolDrawerCheck(_RestToggle):
    _state_attr = "drawer_check"
    _setter = "set_drawer_check"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "drawer_check", "mdi:archive-check")


class XtoolFilterCheck(_RestToggle):
    _state_attr = "filter_check"
    _setter = "set_filter_check"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "filter_check", "mdi:air-filter")


class XtoolPurifierCheck(_RestToggle):
    _state_attr = "purifier_check"
    _setter = "set_purifier_check"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "purifier_check", "mdi:air-purifier")


class XtoolPurifierContinue(_RestToggle):
    _state_attr = "purifier_continue"
    _setter = "set_purifier_continue"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "purifier_continue", "mdi:autorenew")


class XtoolCoolingFan(_RestToggle):
    _state_attr = "cooling_fan_running"
    _setter = "set_cooling_fan"
    _attr_entity_category = None  # primary control, not config

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "cooling_fan", "mdi:fan")


class XtoolSmokingFanRest(_RestToggle):
    _state_attr = "smoking_fan_running"
    _setter = "set_smoking_fan"
    _attr_entity_category = None

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "smoking_fan", "mdi:fan")


# --- Generic REST numbers --------------------------------------------------


class _RestTimeoutNumber(XtoolEntity, NumberEntity):
    """Generic seconds-based timeout number — reads ``state.<attr>``,
    writes via ``coord.protocol.<setter>(int)``."""

    _attr_icon = "mdi:timer-sand"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 30
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _state_attr: str = ""
    _setter: str = ""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        attr: str,
        setter: str,
        max_seconds: int = 3600,
    ) -> None:
        super().__init__(coordinator)
        self._state_attr = attr
        self._setter = setter
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._attr_translation_key = key
        self._attr_native_min_value = 0
        self._attr_native_max_value = max_seconds

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        d = self.coordinator.data
        return bool(d and getattr(d, self._state_attr) is not None)

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        return getattr(d, self._state_attr) if d else None

    async def async_set_native_value(self, value: float) -> None:
        n = int(value)
        await getattr(self.coordinator.protocol, self._setter)(n)
        if self.coordinator.data:
            setattr(self.coordinator.data, self._state_attr, n)
        self.async_write_ha_state()


def _sleep_timeout(coord):
    return _RestTimeoutNumber(coord, "sleep_timeout", "sleep_timeout", "set_sleep_timeout")


def _sleep_timeout_open_gap(coord):
    return _RestTimeoutNumber(
        coord, "sleep_timeout_open_gap", "sleep_timeout_open_gap", "set_sleep_timeout_open_gap"
    )


def _fill_light_auto_off(coord):
    return _RestTimeoutNumber(
        coord, "fill_light_auto_off", "fill_light_auto_off", "set_fill_light_auto_off"
    )


def _ir_light_auto_off(coord):
    return _RestTimeoutNumber(
        coord, "ir_light_auto_off", "ir_light_auto_off", "set_ir_light_auto_off"
    )


# --- Generic REST buttons --------------------------------------------------


class XtoolReboot(XtoolEntity, ButtonEntity):
    """Soft-reboot the device (REST family)."""

    _attr_translation_key = "reboot"
    _attr_icon = "mdi:restart"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_reboot"

    async def async_press(self) -> None:
        await self.coordinator.protocol.reboot()


# --- Generic REST diagnostic sensors ---------------------------------------


class _RestStateSensor(XtoolEntity, SensorEntity):
    """Sensor that reads ``state.<attr>`` on the coordinator."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr: str = ""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        icon: str,
        attr: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr = attr
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"

    @property
    def native_value(self) -> str | int | float | None:
        d = self.coordinator.data
        if d is None:
            return None
        v = getattr(d, self._attr)
        if isinstance(v, str):
            return v or None
        return v


class _RestTempSensor(_RestStateSensor):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1


class _RestFlowSensor(_RestStateSensor):
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.LITERS_PER_MINUTE
    _attr_suggested_display_precision = 2


class _RestNumericSensor(_RestStateSensor):
    """Numeric sensor with optional unit + decimals."""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        icon: str,
        attr: str,
        unit: str | None = None,
        precision: int | None = None,
    ) -> None:
        super().__init__(coordinator, key, icon, attr)
        if unit is not None:
            self._attr_native_unit_of_measurement = unit
        if precision is not None:
            self._attr_suggested_display_precision = precision


class _RestPushBinary(XtoolEntity, BinarySensorEntity):
    """Generic ``state.<attr>`` binary sensor (None = unavailable)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _state_attr: str = ""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        icon: str,
        attr: str,
        device_class: BinarySensorDeviceClass | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._state_attr = attr
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        if device_class is not None:
            self._attr_device_class = device_class

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        d = self.coordinator.data
        return bool(d and getattr(d, self._state_attr) is not None)

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        return getattr(d, self._state_attr) if d else None


class XtoolDisplayBrightness(XtoolEntity, NumberEntity):
    """Touchscreen brightness 0-100 (F1 Ultra)."""

    _attr_translation_key = "display_brightness"
    _attr_icon = "mdi:brightness-percent"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_display_brightness"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        d = self.coordinator.data
        return bool(d and d.display_brightness is not None)

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        return d.display_brightness if d else None

    async def async_set_native_value(self, value: float) -> None:
        n = int(value)
        await self.coordinator.protocol.set_display_brightness(n)
        if self.coordinator.data:
            self.coordinator.data.display_brightness = n
        self.async_write_ha_state()


class XtoolTimeSync(XtoolEntity, ButtonEntity):
    """Trigger device clock sync (F1 Ultra)."""

    _attr_translation_key = "time_sync"
    _attr_icon = "mdi:clock-sync"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_time_sync"

    async def async_press(self) -> None:
        await self.coordinator.protocol.time_sync()


# --- Selects ---------------------------------------------------------------


class XtoolPurifierSpeed(XtoolEntity, SelectEntity):
    """Air-purifier speed (config kv `purifierSpeed`)."""

    _attr_translation_key = "purifier_speed_select"
    _attr_icon = "mdi:fan"
    _attr_entity_category = EntityCategory.CONFIG
    _OPTIONS = ["off", "low", "medium", "high"]
    _attr_options = _OPTIONS

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_purifier_speed_select"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        d = self.coordinator.data
        return bool(d and d.purifier_speed is not None)

    @property
    def current_option(self) -> str | None:
        d = self.coordinator.data
        if d is None:
            return None
        v = d.purifier_speed or 0
        if 0 <= v < len(self._OPTIONS):
            return self._OPTIONS[v]
        return None

    async def async_select_option(self, option: str) -> None:
        if option not in self._OPTIONS:
            return
        v = self._OPTIONS.index(option)
        await self.coordinator.protocol.set_purifier_speed(v)
        if self.coordinator.data:
            self.coordinator.data.purifier_speed = v
        self.async_write_ha_state()


class XtoolFlameLevelHL(XtoolEntity, SelectEntity):
    """Flame-detection level (config kv `flameLevelHLSelect`, 1=high, 2=low)."""

    _attr_translation_key = "flame_level_hl"
    _attr_icon = "mdi:fire-alert"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = ["high", "low"]

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_flame_level_hl"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        d = self.coordinator.data
        return bool(d and d.flame_level_hl is not None)

    @property
    def current_option(self) -> str | None:
        d = self.coordinator.data
        if d is None or d.flame_level_hl is None:
            return None
        return "high" if d.flame_level_hl == 1 else "low"

    async def async_select_option(self, option: str) -> None:
        v = 1 if option == "high" else 2
        await self.coordinator.protocol.set_flame_level_hl(v)
        if self.coordinator.data:
            self.coordinator.data.flame_level_hl = v
        self.async_write_ha_state()


# --- Firmware update -------------------------------------------------------


class RestFirmwareUpdate(XtoolFirmwareUpdate):
    """REST firmware update — machine_type is set once on RestCoordinator init."""


def build_rest_updates(coordinator: XtoolCoordinator) -> list[UpdateEntity]:
    if coordinator.model.firmware_content_id:
        return [RestFirmwareUpdate(coordinator)]
    return []
