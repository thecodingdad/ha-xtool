"""The P2's entities, named and written the way xTool Studio does.

Settings are Studio's ``/config/set`` keys (Studio's Device settings for
the P2). Each fill light is its own light. Entities for P2 endpoints that
refuse or ignore the request are not created (see docs/PROTOCOL.md).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.camera import Camera
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.components.lock import LockEntity
from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfLength, UnitOfTime
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity

from ...const import XtoolStatus
from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity, XtoolReadOnlyEntity, XtoolRestoringSensor
from ..ws_v2.camera_correction import CameraCalibration, CameraView, Overlay, SurfaceLift
from ..ws_v2.entities import (
    _camera_view,
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
from . import p2_geometry
from .protocol import REST_BRIGHTNESS_MAX

_LOGGER = logging.getLogger(__name__)

P2_EXPOSURE_MAX = 1000
P2_EXPOSURE_STEP = 10
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
        if data is not None and p2_view_needs_head(self.coordinator, self._camera_name):
            # Overlays and the close camera preset follow the head
            position = await self.coordinator.protocol.read_head_position()
            if position is not None:
                data.position_x, data.position_y = position
        return await super()._image(max_age)

    def _correction(self) -> dict[str, Any]:
        """The crop preset, the material's height and the overlays."""
        return p2_correction(
            self.coordinator, self._camera_name, self._calibration, super()._correction()["view"],
        )


class P2CameraExposure(_CameraDeviceEntity, XtoolCameraExposure):
    """A camera's exposure, on that camera's device.

    Studio sets 80-1000 (overview 100, or 1000 with Background brightness
    increase; close camera 150, or 500) and waits a second before the photo.
    """

    _attr_native_max_value = P2_EXPOSURE_MAX
    _attr_native_step = P2_EXPOSURE_STEP
    _attr_mode = NumberMode.SLIDER

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


# --- Camera views: crop presets, overlays, material height ----------------

VIEW_NONE = "none"
VIEW_CUSTOM = "custom"
VIEW_CLOSE_CAMERA = "close_camera"
P2_CROP_OPTIONS = [VIEW_CUSTOM, *p2_geometry.AREAS, VIEW_CLOSE_CAMERA]
P2_OVERLAY_OPTIONS = [VIEW_NONE, *p2_geometry.AREAS, VIEW_CLOSE_CAMERA, VIEW_CUSTOM]
P2_MODE_OPTIONS = list(p2_geometry.MODES)


def _p2_view(coordinator: XtoolCoordinator) -> dict[str, Any]:
    """The P2 cameras' view settings (kept by Home Assistant)."""
    view = getattr(coordinator, "_p2_view", None)
    if view is None:
        view = {
            "crop_preset": VIEW_CUSTOM,
            "overlay": VIEW_NONE,
            "processing_mode": "process_on_slats",
            "slat_overlay": False,
            "overlay_closeup": False,
        }
        for key, _minimum, _maximum, default in p2_geometry.MODES.values():
            view[key] = default
        coordinator._p2_view = view
    return view


def _head(coordinator: XtoolCoordinator) -> tuple[float, float]:
    d = coordinator.data
    return (getattr(d, "position_x", 0.0) or 0.0, getattr(d, "position_y", 0.0) or 0.0)


def _measure_spot(coordinator: XtoolCoordinator) -> tuple[float, float]:
    """Where Measure distance reads, for the head's position now."""
    offset = {}
    if coordinator.data is not None:
        try:
            offset = coordinator.protocol.measure_offset(coordinator.data)
        except AttributeError:
            offset = {}
    dx, dy = (
        (offset["x"], offset["y"]) if isinstance(offset, dict) and offset
        else p2_geometry.MEASURE_OFFSET
    )
    x, y = _head(coordinator)
    return x + dx, y + dy


def _area(coordinator: XtoolCoordinator, name: str) -> tuple[float, float, float, float] | None:
    if name == VIEW_CLOSE_CAMERA:
        return p2_geometry.close_camera_area(*_head(coordinator))
    return p2_geometry.AREAS.get(name)


def _custom_rect(
    coordinator: XtoolCoordinator, calibration: CameraCalibration,
) -> tuple[float, float, float, float] | None:
    """The overview's crop numbers as a rectangle (none for a side of 0)."""
    v = _camera_view(coordinator, "overview")
    if v.width_mm <= 0 or v.height_mm <= 0:
        return None
    return (
        calibration.area_mm / 2 + v.center_x_mm - v.width_mm / 2,
        calibration.area_h_mm / 2 + v.center_y_mm - v.height_mm / 2,
        v.width_mm,
        v.height_mm,
    )


def p2_correction(
    coordinator: XtoolCoordinator, camera_name: str, calibration: CameraCalibration,
    view: CameraView,
) -> dict[str, Any]:
    """``correct_image`` arguments for a P2 camera's next photo."""
    settings = _p2_view(coordinator)
    if camera_name != "overview":
        if not settings.get(f"overlay_{camera_name}"):
            return {"view": view}
        # The close camera's picture moves with the head
        left, top, _, _ = p2_geometry.close_camera_area(*_head(coordinator))
        spot_x, spot_y = _measure_spot(coordinator)
        width, height = p2_geometry.CLOSE_CAMERA_SIZE
        return {
            "view": view,
            "overlay": Overlay(
                lines=tuple(
                    (x - left, 0.0, x - left, height, label)
                    for x, label in p2_geometry.slats()
                    if 0 <= x - left <= width
                ),
                markers=((spot_x - left, spot_y - top),),
            ),
        }
    crop = settings["crop_preset"]
    if crop != VIEW_CUSTOM:
        x, y, w, h = _area(coordinator, crop)
        view = CameraView(
            w, h,
            x + w / 2 - calibration.area_mm / 2,
            y + h / 2 - calibration.area_h_mm / 2,
        )
    mode = settings["processing_mode"]
    key = p2_geometry.MODES[mode][0]
    surface = p2_geometry.surface_height(mode, settings[key]) - calibration.plane_mm
    camera_x, camera_y, camera_height = p2_geometry.OVERVIEW_CAMERA
    lift = SurfaceLift(camera_x, camera_y, camera_height, surface) if surface else None
    rects = []
    overlay_name = settings["overlay"]
    if overlay_name == VIEW_CUSTOM:
        rect = _custom_rect(coordinator, calibration)
        if rect is not None:
            rects.append(rect)
    elif overlay_name != VIEW_NONE:
        rects.append(_area(coordinator, overlay_name))
    lines = ()
    if settings["slat_overlay"]:
        _, top, _, height = p2_geometry.AREAS["work_bed"]
        lines = tuple((x, top, x, top + height, label) for x, label in p2_geometry.slats())
    overlay = None
    if rects or lines:
        overlay = Overlay(rects=tuple(rects), lines=lines, markers=(_measure_spot(coordinator),))
    return {"view": view, "lift": lift, "overlay": overlay}


def p2_view_needs_head(coordinator: XtoolCoordinator, camera_name: str) -> bool:
    """Whether the next picture depends on where the head is."""
    settings = _p2_view(coordinator)
    if camera_name != "overview":
        return bool(settings.get(f"overlay_{camera_name}"))
    return (
        settings["crop_preset"] == VIEW_CLOSE_CAMERA
        or settings["overlay"] != VIEW_NONE
        or settings["slat_overlay"]
    )


class _P2ViewSetting(_CameraDeviceEntity):
    """A P2 camera view setting, kept by Home Assistant (not the laser)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: XtoolCoordinator, camera_name: str, key: str) -> None:
        super().__init__(coordinator)
        self._camera_name = camera_name
        self._key = key
        self._set_unique_id(f"camera_{camera_name}_{key}")
        self._use_camera_device(camera_name)
        self._attr_translation_key = key.removesuffix(f"_{camera_name}")

    @property
    def available(self) -> bool:
        return True

    @property
    def _value(self) -> Any:
        return _p2_view(self.coordinator)[self._key]

    async def _store(self, value: Any) -> None:
        _p2_view(self.coordinator)[self._key] = value
        self.async_write_ha_state()
        camera = _thumbnail_registry(self.coordinator).cameras.get(self._camera_name)
        if camera is not None:
            await camera.refresh_picture()


class P2ViewSelect(_P2ViewSetting, SelectEntity, RestoreEntity):
    """Crop preset, overlay, or processing mode of the overview camera."""

    def __init__(
        self, coordinator: XtoolCoordinator, camera_name: str, key: str, options: list[str],
    ) -> None:
        super().__init__(coordinator, camera_name, key)
        self._attr_options = options

    @property
    def current_option(self) -> str:
        return self._value

    async def async_select_option(self, option: str) -> None:
        await self._store(option)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            _p2_view(self.coordinator)[self._key] = last.state


class P2ViewSwitch(_P2ViewSetting, SwitchEntity, RestoreEntity):
    """Draw the slats (and the measuring spot) on a camera's picture."""

    @property
    def is_on(self) -> bool:
        return bool(self._value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._store(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._store(False)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            _p2_view(self.coordinator)[self._key] = last.state == "on"


class P2MaterialNumber(_P2ViewSetting, RestoreNumber):
    """A processing mode's material setting, as Studio enters it.

    Process on slats: the material's thickness. Honeycomb panel and Slats
    removed: Studio's distance (25 mm above the slats minus the material's
    top). The overview picture shows the material's top to scale.
    """

    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: XtoolCoordinator, mode: str) -> None:
        key, minimum, maximum, _default = p2_geometry.MODES[mode]
        super().__init__(coordinator, "overview", key)
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum

    @property
    def native_value(self) -> float:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        await self._store(float(value))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            value = min(max(float(last.native_value), self._attr_native_min_value),
                        self._attr_native_max_value)
            _p2_view(self.coordinator)[self._key] = value


def build_p2_view_selects(coordinator: XtoolCoordinator) -> list[SelectEntity]:
    if "overview" not in coordinator.model.camera_names:
        return []
    return [
        P2ViewSelect(coordinator, "overview", "crop_preset", P2_CROP_OPTIONS),
        P2ViewSelect(coordinator, "overview", "overlay", P2_OVERLAY_OPTIONS),
        P2ViewSelect(coordinator, "overview", "processing_mode", P2_MODE_OPTIONS),
    ]


def build_p2_view_switches(coordinator: XtoolCoordinator) -> list[SwitchEntity]:
    names = coordinator.model.camera_names
    entities: list[SwitchEntity] = []
    if "overview" in names:
        entities.append(P2ViewSwitch(coordinator, "overview", "slat_overlay"))
    if "closeup" in names:
        entities.append(P2ViewSwitch(coordinator, "closeup", "overlay_closeup"))
    return entities


def build_p2_view_numbers(coordinator: XtoolCoordinator) -> list[NumberEntity]:
    if "overview" not in coordinator.model.camera_names:
        return []
    return [P2MaterialNumber(coordinator, mode) for mode in p2_geometry.MODES]


# --- Laser head ------------------------------------------------------------

# The head moves only while the P2 waits for work, as in Studio
P2_HEAD_MOVE_STATUSES = (XtoolStatus.IDLE, XtoolStatus.FINISHED)
# Studio's distance in the modes without slats (Honeycomb panel, Slats
# removed) is measured down from this height above the slats
P2_DISTANCE_ZERO_MM = 25.0


def _head_lock(coordinator: XtoolCoordinator) -> asyncio.Lock:
    """One move at a time: a move asked for during another waits for it."""
    lock = getattr(coordinator, "_p2_head_lock", None)
    if lock is None:
        lock = coordinator._p2_head_lock = asyncio.Lock()
    return lock


def _axis_limit_message(
    axis: str, target: float, wanted: float, highest: float,
    step: float | None, steps: int, current: float | None,
) -> str:
    """Why an axis stopped at a limit, as the F2's focus says it."""
    limit = "lower" if target == 0 else "upper"
    text = f"{axis} moved to {target:.2f} mm, the {limit} limit."
    if step is not None and current is not None:
        count = "1 step" if steps == 1 else f"{steps} steps"
        return (
            f"{text} {count} of {step:+.2f} mm from {current:.2f} mm "
            f"would have gone to {wanted:.2f} mm."
        )
    return f"{text} {wanted:.2f} mm is outside 0-{highest:.0f} mm."


async def async_move_laser_head(
    coordinator: XtoolCoordinator,
    x: float | None = None,
    y: float | None = None,
    step_x: float | None = None,
    step_y: float | None = None,
    steps: int = 1,
) -> dict[str, float]:
    """Move the head to x and y, or by signed steps, in mm from home.

    An axis with neither stays where it is. Moves run one at a time, in
    the order asked: each starts when the one before has arrived, and a
    step starts from the position the laser reports then. A move past
    Studio's limits stops at the limit and then raises an error that
    says so, so Home Assistant shows the message (the move was made).
    """
    from .p2 import P2_HEAD_MAX_X, P2_HEAD_MAX_Y

    async with _head_lock(coordinator):
        protocol = coordinator.protocol
        data = coordinator.data
        status = getattr(data, "status", None)
        if status not in P2_HEAD_MOVE_STATUSES:
            raise HomeAssistantError(
                f"Laser head not moved: the laser is {status or 'not connected'}, not idle"
            )
        current: tuple[float, float] | None = None
        if x is None or y is None:
            current = await protocol.read_head_position()
            if current is None:
                raise HomeAssistantError(
                    "Laser head not moved: the laser did not say where the head is"
                )
        axes = []
        for name, value, step, index, highest in (
            ("X", x, step_x, 0, P2_HEAD_MAX_X),
            ("Y", y, step_y, 1, P2_HEAD_MAX_Y),
        ):
            now = current[index] if current is not None else None
            if value is None:
                value = now + (step or 0.0) * steps
                stepped = step if step else None
            else:
                stepped = None
            wanted = round(value, 2)
            target = round(min(max(value, 0.0), highest), 2)
            message = None
            if target != wanted:
                message = _axis_limit_message(
                    name, target, wanted, highest, stepped, steps, now,
                )
            axes.append((target, message))
        (target_x, message_x), (target_y, message_y) = axes
        await protocol.move_laser_head(target_x, target_y)
        if data is not None:
            data.position_x, data.position_y = target_x, target_y
            coordinator.async_set_updated_data(data)
    messages = [m for m in (message_x, message_y) if m]
    if messages:
        message = "Laser head: " + " ".join(messages)
        _LOGGER.warning("%s", message)
        # Reported as an error so Home Assistant shows it; the move was made
        raise HomeAssistantError(message)
    return {"x": target_x, "y": target_y}


class P2LaserPosition(XtoolEntity, NumberEntity):
    """Where the laser head is, in mm from home; setting it moves the head.

    X grows to the right and Y toward the front. The ``xtool.move``
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


class P2Measured(XtoolRestoringSensor, SensorEntity):
    """The last Measure distance, as Studio enters it for each mode.

    Thickness (Process on slats): the material's height above the slats.
    Distance (Honeycomb panel and Slats removed): how far the material's
    top is below 25 mm above the slats. Attributes: where on the bed it
    was measured (the IR sensor's position, mm from home) and the distance
    the sensor read.
    """

    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: XtoolCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = key
        self._set_unique_id(key)

    def _live(self) -> float | None:
        d = self.coordinator.data
        thickness = d.measured_thickness_mm if d else None
        if thickness is None:
            return None
        if self._key == "measured_distance":
            return round(P2_DISTANCE_ZERO_MM - thickness, 2)
        return thickness

    @property
    def native_value(self) -> float | None:
        return self._value_or_restored(self._live())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        d = self.coordinator.data
        if d is None or d.measured_thickness_mm is None:
            return None
        attributes: dict[str, Any] = {"sensor_distance": d.last_distance_mm}
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
        *build_p2_view_switches(coordinator),
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
        *build_p2_view_numbers(coordinator),
    ]


def build_p2_selects(coordinator: XtoolCoordinator) -> list[SelectEntity]:
    return [P2FlameAlarm(coordinator), *build_p2_view_selects(coordinator)]


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
