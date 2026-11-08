"""The P2's entities, named and written the way xTool Studio does.

Settings are Studio's ``/config/set`` keys (Studio's Device settings for
the P2). Each fill light is its own light. Entities for P2 endpoints that
refuse or ignore the request are not created (see docs/PROTOCOL.md).
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.camera import Camera
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.components.lock import LockEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfLength, UnitOfTime
from homeassistant.exceptions import HomeAssistantError

from ...const import XtoolStatus
from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity, XtoolReadOnlyEntity, XtoolRestoringSensor
from ..ws_v2.entities import (
    _CameraDeviceEntity,
    _crop_settings,
    _thumbnail_registry,
    _WSV2Camera,
    _WSV2CameraThumbnailRefresh,
    _WSV2CameraViewNumber,
)
from .entities import (
    XtoolCameraExposure,
    XtoolHomeLaserHead,
    XtoolIRLED,
    XtoolMeasureDistance,
    _RestCoverSensor,
    _RestPushBinary,
)
from .protocol import REST_BRIGHTNESS_MAX

P2_EXPOSURE_MAX = 1000
P2_EXPOSURE_SETTLE_SECONDS = 1.0

# The P2's fill lights by /peripheral/fill_light index
P2_FILL_LIGHTS = (1, 2, 3)
# The right and left lights, which Studio's Fill light brightness sets
P2_STUDIO_FILL_LIGHTS = (2, 3)


def _renamed(entity: Any, translation_key: str) -> Any:
    """The entity under Studio's name (its unique ID stays)."""
    entity._attr_translation_key = translation_key
    return entity


def _off_by_default(entity: Any) -> Any:
    """The entity, off until turned on in Home Assistant."""
    entity._attr_entity_registry_enabled_default = False
    return entity


# --- Lights ----------------------------------------------------------------


class P2FillLight(_CameraDeviceEntity, LightEntity):
    """One of the P2's fill lights (``/peripheral/fill_light`` index 1-3).

    The close light (1) lights the close camera's view, so it is on that
    camera's device.
    """

    _attr_icon = "mdi:track-light"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: XtoolCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._set_unique_id(f"fill_light_{index}")
        self._attr_translation_key = f"fill_light_{index}"
        if index == 1 and self._use_camera_device("closeup"):
            self._attr_translation_key = "fill_light"

    def _level(self) -> int | None:
        data = self.coordinator.data
        return None if data is None else data.fill_light_levels.get(self._index)

    @property
    def available(self) -> bool:
        return super().available and self._level() is not None

    @property
    def is_on(self) -> bool | None:
        level = self._level()
        return None if level is None else level > 0

    @property
    def brightness(self) -> int | None:
        # The laser's 0-255 is Home Assistant's 0-255
        return self._level()

    async def _write(self, level: int) -> None:
        data = self.coordinator.data
        if data is None:
            return
        levels = {**data.fill_light_levels, self._index: level}
        await self.coordinator.async_write(
            {"fill_light_levels": levels},
            self.coordinator.protocol.set_fill_light_level(self._index, level),
        )
        if self._index in P2_STUDIO_FILL_LIGHTS:
            await self._write_studio_brightness(levels)

    async def _write_studio_brightness(self, levels: dict[int, int]) -> None:
        """Studio has one brightness for the right and left lights: the brighter of the two."""
        data = self.coordinator.data
        studio = max(levels.get(index) or 0 for index in P2_STUDIO_FILL_LIGHTS)
        if data is None or not studio or studio == data.device_config.get("fillLightBrightness"):
            return
        protocol = self.coordinator.protocol

        async def send() -> None:
            if not await protocol.set_config(fillLightBrightness=studio):
                raise HomeAssistantError("The P2 did not accept fillLightBrightness")

        await self.coordinator.async_write(
            {"device_config": {**data.device_config, "fillLightBrightness": studio}}, send(),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            await self._write(int(kwargs[ATTR_BRIGHTNESS]))
            return
        # Without a brightness, use Studio's Fill light brightness, as Studio does
        data = self.coordinator.data
        stored = data.device_config.get("fillLightBrightness") if data is not None else None
        level = int(stored) if isinstance(stored, (int, float)) and stored > 0 else REST_BRIGHTNESS_MAX
        await self._write(level)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(0)


# --- Cameras ----------------------------------------------------------------


class P2Camera(_WSV2Camera):
    """A P2 camera on its own device, straightened with the P2's calibration.

    Photos come from ``/camera/snap`` (port 8329). A photo wakes a
    sleeping P2, so while it sleeps the last photo is shown.
    """

    async def _image(self, max_age: Any) -> bytes | None:
        data = self.coordinator.data
        if data is not None and data.status == XtoolStatus.SLEEPING:
            return self._last_snapshot
        return await super()._image(max_age)


class P2CameraExposure(_CameraDeviceEntity, XtoolCameraExposure):
    """A camera's exposure, on that camera's device.

    Studio sets 80-1000 (overview 100, or 1000 with Background brightness
    increase; close camera 150, or 500) and waits a second before the photo.
    """

    _attr_native_max_value = P2_EXPOSURE_MAX

    def __init__(self, coordinator: XtoolCoordinator, stream: str) -> None:
        super().__init__(coordinator, stream)
        if self._use_camera_device(stream):
            self._attr_translation_key = "exposure"

    async def async_set_native_value(self, value: float) -> None:
        await super().async_set_native_value(value)
        camera = _thumbnail_registry(self.coordinator).cameras.get(self._stream_name)
        if camera is not None:
            # The P2 takes the new exposure about a second later
            await asyncio.sleep(P2_EXPOSURE_SETTLE_SECONDS)
            await camera.refresh_picture()


def _p2_camera_keys(coordinator: XtoolCoordinator) -> list[tuple[str, str]]:
    """``(entity key, camera name)``: the keys the P2's cameras always had."""
    return [(f"camera_{name}", name) for name in coordinator.model.camera_names]


def build_p2_cameras(coordinator: XtoolCoordinator) -> list[Camera]:
    icons = {"overview": "mdi:camera", "closeup": "mdi:camera-burst"}
    return [
        P2Camera(coordinator, key, name, icons.get(name), key)
        for key, name in _p2_camera_keys(coordinator)
    ]


# --- Settings (Studio's /config/set keys) ----------------------------------


class _P2Setting(XtoolEntity):
    """A setting from Studio's Device settings, read and written by its key."""

    _attr_entity_category = EntityCategory.CONFIG
    _config_key: str = ""

    def __init__(self, coordinator: XtoolCoordinator, unique_id: str, translation_key: str) -> None:
        super().__init__(coordinator)
        self._set_unique_id(unique_id)
        self._attr_translation_key = translation_key

    def _value(self) -> Any:
        data = self.coordinator.data
        return None if data is None else data.device_config.get(self._config_key)

    @property
    def available(self) -> bool:
        return super().available and self._value() is not None

    async def _write(self, **kv: Any) -> None:
        data = self.coordinator.data
        if data is None:
            return

        async def send() -> None:
            if not await self.coordinator.protocol.set_config(**kv):
                raise HomeAssistantError(f"The P2 did not accept {', '.join(kv)}")

        # Shown at once; the settings are read back only every 60 s
        await self.coordinator.async_write({"device_config": {**data.device_config, **kv}}, send())


class P2Buzzer(_P2Setting, SwitchEntity):
    """Studio: Buzzer."""

    _config_key = "beepEnable"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "beep_enable", "buzzer")

    @property
    def is_on(self) -> bool | None:
        value = self._value()
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(beepEnable=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(beepEnable=False)


class P2ExhaustFan(_P2Setting, SwitchEntity):
    """Studio: Exhaust fan (runs after processing; Studio sets 5 s when turned on)."""

    _config_key = "purifierEnable"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        # A setting, not the fan itself (Exhaust fan running shows the fan)
        super().__init__(coordinator, "smoking_fan", "exhaust_fan_setting")

    @property
    def is_on(self) -> bool | None:
        value = self._value()
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(purifierEnable=True)
        await self._write(purifierTimeout=5)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(purifierEnable=False)


class P2ExhaustTime(_P2Setting, NumberEntity):
    """Studio: Exhaust time after processing, 1-60 s."""

    _config_key = "purifierTimeout"
    _attr_icon = "mdi:timer-sand"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "smoking_fan_duration", "smoking_fan_duration")

    @property
    def native_value(self) -> float | None:
        value = self._value()
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self._write(purifierTimeout=int(value))


class P2LowestOutputPower(_P2Setting, NumberEntity):
    """Studio: Lowest output power, 1-10 % (stored as ``M37S<n>``)."""

    _config_key = "motionPowerCut"
    _attr_icon = "mdi:flash-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "lowest_output_power", "lowest_output_power")

    @property
    def native_value(self) -> float | None:
        value = self._value()
        if value is None:
            return None
        try:
            return float(str(value).split("M37S")[1])
        except (IndexError, ValueError):
            return 5.0  # Studio shows 5 when the value is empty

    async def async_set_native_value(self, value: float) -> None:
        await self._write(motionPowerCut=f"M37S{int(value)}")


class P2FlameAlarm(_P2Setting, SelectEntity):
    """Studio: Flame alarm, Low or High (firmware 25 and later)."""

    _config_key = "flameAlarmLevel1"
    _attr_icon = "mdi:fire-alert"
    _attr_options = ["low", "high"]

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "flame_alarm_level", "flame_alarm_level")

    @property
    def current_option(self) -> str | None:
        value = self._value()
        return None if value is None else ("high" if value else "low")

    async def async_select_option(self, option: str) -> None:
        # Studio's handleControlFlame: the level and the High switch together
        await self._write(flameLevelHLSelect=1, flameAlarmLevel1=option == "high")


# --- Sensors ----------------------------------------------------------------


class P2Baseplate(_RestPushBinary):
    """Studio's baseplate: on when the P2 detects it in place.

    ``/peripheral/drawer`` reads "off" and the DRAWER CLOSE push arrives
    when the baseplate is in (Studio's drawerStatus is ``state === 'off'``).
    """

    _attr_entity_category = None

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "baseplate", "drawer_open")

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        live = getattr(d, self._state_attr) if d else None
        return self._is_on_or_restored(None if live is None else not live)


class P2Connection(XtoolEntity, SensorEntity):
    """Whether Home Assistant reaches the P2 over Ethernet or Wi-Fi."""

    _attr_translation_key = "connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["ethernet", "wifi"]

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("connection")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.protocol.connection()


class P2SafetyLock(XtoolEntity, LockEntity):
    """Studio's Safety lock: locks the lid."""

    _attr_translation_key = "safety_lock"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        # The unique ID the switch had
        self._set_unique_id("digital_lock")

    @property
    def is_locked(self) -> bool | None:
        d = self.coordinator.data
        return d.cover_locked if d else None

    async def async_lock(self, **kwargs: Any) -> None:
        await self.coordinator.async_write(
            {"cover_locked": True}, self.coordinator.protocol.set_digital_lock(True),
        )

    async def async_unlock(self, **kwargs: Any) -> None:
        await self.coordinator.async_write(
            {"cover_locked": False}, self.coordinator.protocol.set_digital_lock(False),
        )


class P2Alarm(XtoolReadOnlyEntity, BinarySensorEntity):
    """On while the P2 reports an alarm; each alarm's code and Studio's message as attributes."""

    _attr_translation_key = "alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("alarm")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return None if data is None else bool(data.active_alarms)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        alarms = [
            {k: a.get(k) for k in ("code", "message", "level")}
            for a in (data.active_alarms if data is not None else [])
        ]
        return {"alarms": alarms, "codes": [a["code"] for a in alarms]}


# --- Laser head ------------------------------------------------------------

# The head moves only while the P2 waits for work, as in Studio
P2_HEAD_MOVE_STATUSES = (XtoolStatus.IDLE, XtoolStatus.FINISHED)


async def async_move_laser_head(
    coordinator: XtoolCoordinator,
    x: float | None = None,
    y: float | None = None,
    step_x: float | None = None,
    step_y: float | None = None,
    steps: int = 1,
) -> dict[str, float]:
    """Move the head to x and y, or by signed steps, in mm from home.

    An axis with neither stays where it is. The position is read from the
    laser first, so a step starts where the head really is. A move past
    Studio's limits stops at the limit and then raises an error that says
    so, so Home Assistant shows the message.
    """
    from .p2 import P2_HEAD_MAX_X, P2_HEAD_MAX_Y

    protocol = coordinator.protocol
    data = coordinator.data
    status = getattr(data, "status", None)
    if status not in P2_HEAD_MOVE_STATUSES:
        raise HomeAssistantError(
            f"Laser head not moved: the laser is {status or 'not connected'}, not idle"
        )
    if x is None or y is None:
        current = await protocol.read_head_position()
        if current is None:
            raise HomeAssistantError(
                "Laser head not moved: the laser did not say where the head is"
            )
        x = current[0] + (step_x or 0.0) * steps if x is None else x
        y = current[1] + (step_y or 0.0) * steps if y is None else y
    wanted = (round(x, 2), round(y, 2))
    target = (
        round(min(max(x, 0.0), P2_HEAD_MAX_X), 2),
        round(min(max(y, 0.0), P2_HEAD_MAX_Y), 2),
    )
    await protocol.move_laser_head(*target)
    if data is not None:
        data.position_x, data.position_y = target
        coordinator.async_set_updated_data(data)
    if target != wanted:
        # Reported as an error so Home Assistant shows it; the move was made
        raise HomeAssistantError(
            f"Laser head moved to X {target[0]:.2f}, Y {target[1]:.2f} mm, at the limit. "
            f"X {wanted[0]:.2f}, Y {wanted[1]:.2f} mm is outside "
            f"0-{P2_HEAD_MAX_X:.0f} by 0-{P2_HEAD_MAX_Y:.0f} mm."
        )
    return {"x": target[0], "y": target[1]}


class P2LaserPosition(XtoolEntity, NumberEntity):
    """Where the laser head is, in mm from home; setting it moves the head.

    X grows to the right and Y toward the front. The ``xtool.move_laser_head``
    action also moves the head in steps.
    """

    # Not a distance number: the xtool.focus action targets those
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_native_min_value = 0.0
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: XtoolCoordinator, axis: str) -> None:
        from .p2 import P2_HEAD_MAX_X, P2_HEAD_MAX_Y

        super().__init__(coordinator)
        self._axis = axis
        self._attr_translation_key = f"laser_position_{axis}"
        self._set_unique_id(f"laser_position_{axis}")
        self._attr_native_max_value = P2_HEAD_MAX_X if axis == "x" else P2_HEAD_MAX_Y

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        return getattr(d, f"position_{self._axis}", None) if d else None

    async def async_set_native_value(self, value: float) -> None:
        await self.async_move_laser_head(**{self._axis: value})

    async def async_move_laser_head(self, **move: Any) -> dict[str, float]:
        return await async_move_laser_head(self.coordinator, **move)


class P2MeasureDistance(XtoolMeasureDistance):
    """Reads the material under the IR sensor, beside the laser.

    The thickness is the P2's own reading at thickness 0 (its
    ``laserMeasureOffset`` setting) minus the distance read.
    """

    async def async_press(self) -> None:
        protocol = self.coordinator.protocol
        reading = await protocol.measure_material()
        data = self.coordinator.data
        if reading is None or data is None:
            raise HomeAssistantError("The laser did not report a distance")
        data.last_distance_mm = reading["distance"]
        offset = protocol.measure_offset(data)
        if offset:
            data.measured_thickness_mm = round(offset["z"] - reading["distance"], 2)
            if "coord_x" in reading and "coord_y" in reading:
                data.measured_at = (
                    round(reading["coord_x"] + offset["x"], 2),
                    round(reading["coord_y"] + offset["y"], 2),
                )
        self.coordinator.async_set_updated_data(data)


class P2MeasuredThickness(XtoolRestoringSensor, SensorEntity):
    """The material thickness from the last Measure distance.

    Attributes: where on the bed it was measured (the IR sensor's position,
    mm from home) and the distance read.
    """

    _attr_translation_key = "measured_thickness"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("measured_thickness")

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        return self._value_or_restored(d.measured_thickness_mm if d else None)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        d = self.coordinator.data
        if d is None or d.measured_thickness_mm is None:
            return None
        attributes: dict[str, Any] = {"distance": d.last_distance_mm}
        if d.measured_at is not None:
            attributes["x"], attributes["y"] = d.measured_at
        return attributes


# --- Builders ----------------------------------------------------------------


def build_p2_switches(coordinator: XtoolCoordinator) -> list[SwitchEntity]:
    return [
        _renamed(XtoolIRLED(coordinator, "close"), "aimed_measure_light"),
        _renamed(XtoolIRLED(coordinator, "global"), "quick_measure_light"),
        P2Buzzer(coordinator),
        P2ExhaustFan(coordinator),
    ]


def build_p2_locks(coordinator: XtoolCoordinator) -> list[LockEntity]:
    return [P2SafetyLock(coordinator)]


def build_p2_numbers(coordinator: XtoolCoordinator) -> list[NumberEntity]:
    cameras = _p2_camera_keys(coordinator)
    return [
        *(P2CameraExposure(coordinator, name) for _, name in cameras),
        *(_WSV2CameraThumbnailRefresh(coordinator, key, name) for key, name in cameras),
        *(
            _WSV2CameraViewNumber(coordinator, *setting, camera_name=name)
            for _, name in cameras
            for setting in _crop_settings(coordinator, name)
        ),
        P2ExhaustTime(coordinator),
        P2LowestOutputPower(coordinator),
        P2LaserPosition(coordinator, "x"),
        P2LaserPosition(coordinator, "y"),
    ]


def build_p2_selects(coordinator: XtoolCoordinator) -> list[SelectEntity]:
    return [P2FlameAlarm(coordinator)]


def build_p2_lights(coordinator: XtoolCoordinator) -> list[LightEntity]:
    return [P2FillLight(coordinator, index) for index in P2_FILL_LIGHTS]


def build_p2_binary_sensors(coordinator: XtoolCoordinator) -> list[BinarySensorEntity]:
    return [
        _renamed(_RestCoverSensor(coordinator), "lid"),
        P2Baseplate(coordinator),
        P2Alarm(coordinator),
        # Both run whenever the P2 is awake; the alarms report a failure
        _off_by_default(_RestPushBinary(coordinator, "cooling_fan_running", "cooling_fan_running",
                                        BinarySensorDeviceClass.RUNNING)),
        _RestPushBinary(coordinator, "smoking_fan_running", "smoking_fan_running",
                        BinarySensorDeviceClass.RUNNING),
        _off_by_default(_RestPushBinary(coordinator, "water_pump_running", "water_pump_running",
                                        BinarySensorDeviceClass.RUNNING)),
    ]


def build_p2_buttons(coordinator: XtoolCoordinator) -> list[ButtonEntity]:
    return [
        XtoolHomeLaserHead(coordinator),
        P2MeasureDistance(coordinator),
    ]
