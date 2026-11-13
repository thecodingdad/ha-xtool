"""The P2's entities, named and written the way xTool Studio does.

Settings are Studio's ``/config/set`` keys (Studio's Device settings for
the P2). Each fill light is its own light. Entities for P2 endpoints that
refuse or ignore the request are not created (see docs/PROTOCOL.md).
"""

from __future__ import annotations

import asyncio
import logging
import math
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
from homeassistant.util import dt as dt_util

from ...const import CONF_OVERLAY_COLORS, XtoolStatus
from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity, XtoolReadOnlyEntity, XtoolRestoringSensor
from ..ws_v2.camera_correction import (
    CameraCalibration,
    CameraView,
    Overlay,
    SurfaceLift,
    photo_size_mm,
)
from ..ws_v2.entities import (
    _camera_view,
    _camera_view_values,
    _CameraDeviceEntity,
    _clamp_crop,
    _crop_from_origin,
    _crop_numbers,
    _crop_settings,
    MIN_SNAPSHOT_INTERVAL,
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

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: XtoolCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._set_unique_id(f"fill_light_{index}")
        self._attr_translation_key = f"fill_light_{index}"
        if index == 1:
            self._use_camera_device("closeup")

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

    # The last photo is drawn again (while the P2 sleeps) after a change
    _redraw = True

    async def refresh_picture(self) -> None:
        self._redraw = True
        await super().refresh_picture()

    async def _image(self, max_age: Any) -> bytes | None:
        data = self.coordinator.data
        if data is not None and data.status == XtoolStatus.SLEEPING:
            # No new photo, but the last one shows the settings of now
            if self._redraw and self._last_photo is not None and self._calibration is not None:
                self._redraw = False
                self._last_snapshot = await self._corrected(self._last_photo)
            return self._last_snapshot
        if data is not None and p2_view_needs_head(self.coordinator, self._camera_name):
            # Overlays and the close camera preset follow the head
            position = await self.coordinator.protocol.read_head_position()
            if position is not None:
                data.position_x, data.position_y = position
        if (
            self._camera_name == "overview"
            and _p2_view(self.coordinator)[_overlay_key("overview", CLOSE_CAMERA_IMAGE)]
            and (self._last_snapshot is None
                 or dt_util.utcnow() - self._last_snapshot_time >= max_age)
        ):
            # The close camera's picture, as new as this one
            close = _thumbnail_registry(self.coordinator).cameras.get("closeup")
            if close is not None:
                await close._image(MIN_SNAPSHOT_INTERVAL)
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
        if not math.isfinite(value):
            # The slider sends no number while the exposure is unknown
            raise HomeAssistantError(f"Exposure {value} is not a number")
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
    icons = {"overview": "mdi:cctv", "closeup": "mdi:camera-document"}
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

VIEW_CUSTOM = "custom"
VIEW_UNCROPPED = "uncropped"
VIEW_STUDIO = "studio"
VIEW_SYNC_TO_MODE = "sync_to_mode"
# The close camera's picture, seen by the overview camera
CLOSE_CAMERA_PREFIX = "close_camera_"
CLOSE_CAMERA_AREAS = tuple(
    f"{CLOSE_CAMERA_PREFIX}{kind}" for kind in (VIEW_UNCROPPED, VIEW_STUDIO, VIEW_CUSTOM)
)
P2_CROP_OPTIONS: dict[str, list[str]] = {
    "overview": [
        VIEW_UNCROPPED, VIEW_SYNC_TO_MODE, *p2_geometry.AREAS, *CLOSE_CAMERA_AREAS, VIEW_CUSTOM,
    ],
    "closeup": [VIEW_UNCROPPED, VIEW_STUDIO, VIEW_CUSTOM],
}
# The close camera's picture, as that camera shows it, laid on the overview's
CLOSE_CAMERA_IMAGE = "close_camera"
# A processing mode's area (the Mode select's options)
MODE_PREFIX = "mode_"
# Drawn on top of the areas: where the laser fires, the slats, the red dots
OVERLAY_MARKS = ("laser_target", "slat_lines", "red_dot_close", "red_dot_quick")
# Each overlay is a switch; any number may be on
P2_OVERLAYS: dict[str, list[str]] = {
    "overview": [
        *(
            f"{MODE_PREFIX}{area}" if area in p2_geometry.MODES else area
            for area in p2_geometry.AREAS
        ),
        CLOSE_CAMERA_IMAGE, *OVERLAY_MARKS,
    ],
    "closeup": [VIEW_STUDIO, VIEW_CUSTOM, *OVERLAY_MARKS],
}
P2_MODE_OPTIONS = list(p2_geometry.MODES)
# Settings changed while working: on the device page's Controls
P2_CONTROL_SETTINGS = ("crop_preset", "processing_mode", "thickness_or_distance")
P2_VIEW_CAMERAS = ("overview", "closeup")


def _key(camera_name: str, name: str) -> str:
    """A view setting's key: the overview's own name, the others suffixed."""
    return name if camera_name == "overview" else f"{name}_{camera_name}"


def _overlay_key(camera_name: str, overlay: str) -> str:
    return _key(camera_name, f"overlay_{overlay}")


def _p2_view(coordinator: XtoolCoordinator) -> dict[str, Any]:
    """The P2 cameras' view settings (kept by Home Assistant)."""
    view = getattr(coordinator, "_p2_view", None)
    if view is None:
        view = {
            "processing_mode": "process_on_slats",
            "thickness_or_distance": p2_geometry.MODE_DEFAULT,
        }
        for camera_name in P2_VIEW_CAMERAS:
            view[_key(camera_name, "crop_preset")] = VIEW_CUSTOM
            for overlay in P2_OVERLAYS[camera_name]:
                view[_overlay_key(camera_name, overlay)] = False
        coordinator._p2_view = view
    return view


def _head(coordinator: XtoolCoordinator) -> tuple[float, float]:
    d = coordinator.data
    return (getattr(d, "position_x", 0.0) or 0.0, getattr(d, "position_y", 0.0) or 0.0)


def _calibration_of(coordinator: XtoolCoordinator, camera_name: str) -> CameraCalibration | None:
    camera = _thumbnail_registry(coordinator).cameras.get(camera_name)
    return getattr(camera, "_calibration", None)


def _view_rect(
    view: CameraView, area: tuple[float, float], photo: tuple[float, float] | None,
) -> tuple[float, float, float, float] | None:
    """The area a crop shows, in its camera's calibrated mm.

    A side of 0 is the whole photo that way; ``photo`` is that size
    (unknown before the camera's calibration is read).
    """
    width = view.width_mm if view.width_mm > 0 else (photo[0] if photo else 0)
    height = view.height_mm if view.height_mm > 0 else (photo[1] if photo else 0)
    if width <= 0 or height <= 0:
        return None
    return (
        area[0] / 2 + view.center_x_mm - width / 2,
        area[1] / 2 + view.center_y_mm - height / 2,
        width, height,
    )


def _close_origin(coordinator: XtoolCoordinator) -> tuple[float, float]:
    """Where the close camera's calibrated area starts, for the head now."""
    left, top, _, _ = p2_geometry.close_camera_area(*_head(coordinator))
    return left, top


def _close_view(coordinator: XtoolCoordinator, kind: str) -> CameraView:
    """The close camera's crop for Uncropped, Studio or Custom."""
    if kind == VIEW_UNCROPPED:
        return CameraView(0, 0, 0, 0)
    if kind == VIEW_STUDIO:
        return CameraView(*p2_geometry.CLOSE_CAMERA_SIZE, 0, 0)
    return _camera_view(coordinator, "closeup")


def _close_rect(
    coordinator: XtoolCoordinator, kind: str,
) -> tuple[float, float, float, float] | None:
    """What the close camera shows (Uncropped, Studio or Custom), in its mm."""
    calibration = _calibration_of(coordinator, "closeup")
    if calibration is None:
        area, photo = p2_geometry.CLOSE_CAMERA_SIZE, None
    else:
        area = (calibration.area_mm, calibration.area_h_mm)
        photo = photo_size_mm(calibration)
    return _view_rect(_close_view(coordinator, kind), area, photo)


def _overview_area(
    coordinator: XtoolCoordinator, name: str,
) -> tuple[float, float, float, float] | None:
    """A bed area, or what the close camera shows, in the laser's mm."""
    if name in p2_geometry.AREAS:
        return p2_geometry.AREAS[name]
    if name in CLOSE_CAMERA_AREAS:
        rect = _close_rect(coordinator, name.removeprefix(CLOSE_CAMERA_PREFIX))
        if rect is None:
            return None
        left, top = _close_origin(coordinator)
        return (rect[0] + left, rect[1] + top, rect[2], rect[3])
    return None


def _preset_view(
    coordinator: XtoolCoordinator, camera_name: str, preset: str,
    calibration: CameraCalibration,
) -> CameraView | None:
    """The crop that shows a preset (None: Custom, or not known yet).

    As ``correct_image`` takes it: the centre from the calibrated area's
    centre.
    """
    if preset == VIEW_CUSTOM:
        return None
    if preset == VIEW_UNCROPPED:
        return CameraView(0, 0, 0, 0)
    if camera_name != "overview":
        return _close_view(coordinator, preset)
    if preset == VIEW_SYNC_TO_MODE:
        # The selected Mode's area
        preset = _p2_view(coordinator)["processing_mode"]
    rect = _overview_area(coordinator, preset)
    if rect is None:
        return None
    x, y, w, h = rect
    return CameraView(
        w, h, x + w / 2 - calibration.area_mm / 2, y + h / 2 - calibration.area_h_mm / 2,
    )


def _show_crop(
    coordinator: XtoolCoordinator, camera_name: str, view: CameraView,
    calibration: CameraCalibration,
) -> None:
    """Write a preset's crop into the camera's crop numbers.

    The overview's centre numbers are in the laser's mm (from home).
    """
    dx, dy = (
        (calibration.area_mm / 2, calibration.area_h_mm / 2)
        if _crop_from_origin(coordinator, camera_name) else (0.0, 0.0)
    )
    stored = _camera_view_values(coordinator, camera_name)
    numbers = _crop_numbers(coordinator, camera_name)
    settings = _crop_settings(coordinator, camera_name)
    for setting, value in (
        ("width", view.width_mm), ("height", view.height_mm),
        ("center_x", view.center_x_mm + dx), ("center_y", view.center_y_mm + dy),
    ):
        value = _clamp_crop(setting, round(value, 1), settings)
        if stored.get(setting) == value:
            continue
        number = numbers.get(setting)
        if number is not None:
            number.set_crop(value)
        else:
            stored[setting] = value


def _thickness_or_distance(coordinator: XtoolCoordinator) -> tuple[str, float]:
    """The selected Mode and its setting, within the mode's range."""
    settings = _p2_view(coordinator)
    mode = settings["processing_mode"]
    low, high = p2_geometry.MODES[mode]
    return mode, min(max(float(settings["thickness_or_distance"]), low), high)


def _overlay_rect(
    coordinator: XtoolCoordinator, camera_name: str, name: str,
) -> tuple[float, float, float, float] | None:
    """An area overlay's outline, in the laser's mm."""
    if camera_name == "overview":
        return _overview_area(coordinator, name.removeprefix(MODE_PREFIX))
    rect = _close_rect(coordinator, name)
    if rect is None:
        return None
    left, top = _close_origin(coordinator)
    return (rect[0] + left, rect[1] + top, rect[2], rect[3])


def _color(coordinator: XtoolCoordinator, key: str) -> tuple[int, int, int]:
    """An overlay colour: the options' (Configure), or the default."""
    entry = getattr(coordinator, "config_entry", None)
    chosen = (getattr(entry, "options", None) or {}).get(CONF_OVERLAY_COLORS) or {}
    value = chosen.get(key) if isinstance(chosen, dict) else None
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return tuple(min(max(int(c), 0), 255) for c in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            pass
    return p2_geometry.OVERLAY_COLORS[key]


def _area_color(coordinator: XtoolCoordinator, name: str) -> tuple[int, int, int]:
    return _color(coordinator, name.removeprefix(MODE_PREFIX))


def p2_correction(
    coordinator: XtoolCoordinator, camera_name: str, calibration: CameraCalibration,
    view: CameraView,
) -> dict[str, Any]:
    """``correct_image`` arguments for a P2 camera's next photo."""
    settings = _p2_view(coordinator)
    preset = _preset_view(
        coordinator, camera_name, settings[_key(camera_name, "crop_preset")], calibration,
    )
    if preset is not None:
        # The crop numbers show the preset (a close camera area follows the head)
        view = preset
        _show_crop(coordinator, camera_name, view, calibration)

    def on(overlay: str) -> bool:
        return bool(settings[_overlay_key(camera_name, overlay)])

    # Overlays are worked out in the laser's mm, then moved to the
    # camera's calibrated area (the close camera's moves with the head)
    areas = []
    for name in P2_OVERLAYS[camera_name]:
        if name not in OVERLAY_MARKS and name != CLOSE_CAMERA_IMAGE and on(name):
            rect = _overlay_rect(coordinator, camera_name, name)
            if rect is not None:
                areas.append((*rect, _area_color(coordinator, name)))
    images = []
    if camera_name == "overview" and on(CLOSE_CAMERA_IMAGE):
        # The close camera's last picture as it shows it (its crop and
        # overlays), where it was taken
        close = _thumbnail_registry(coordinator).cameras.get("closeup")
        picture = getattr(close, "_last_snapshot", None)
        rect = _overlay_rect(coordinator, "closeup", VIEW_CUSTOM)
        if picture and rect is not None:
            images.append((*rect, picture))
    lines = ()
    if on("slat_lines"):
        _, top, _, height = p2_geometry.AREAS["work_bed"]
        # Every slat's line; the odd numbers written
        lines = tuple(
            (x, top, x, top + height, label if int(label) % 2 else "")
            for x, label in p2_geometry.slats()
        )
    head_x, head_y = _head(coordinator)
    markers = ((head_x, head_y),) if on("laser_target") else ()
    spots = []
    rings = []
    if on("red_dot_close"):
        # A ring the dot sits just inside, so the dot itself stays visible
        dx, dy = p2_geometry.CLOSE_RED_DOT_OFFSET
        rings.append((head_x + dx, head_y + dy, p2_geometry.CLOSE_RED_DOT_DIAMETER))
    if on("red_dot_quick"):
        spots.append((
            *p2_geometry.QUICK_RED_DOT, *p2_geometry.QUICK_RED_DOT_SIZE,
            p2_geometry.quick_red_dot_angle(),
        ))
    overlay = None
    if areas or lines or markers or spots or rings or images:
        dx, dy = (0.0, 0.0) if camera_name == "overview" else _close_origin(coordinator)
        overlay = Overlay(
            areas=tuple((x - dx, y - dy, w, h, color) for x, y, w, h, color in areas),
            lines=tuple((x0 - dx, y0 - dy, x1 - dx, y1 - dy, t) for x0, y0, x1, y1, t in lines),
            markers=tuple((x - dx, y - dy) for x, y in markers),
            spots=tuple((x - dx, y - dy, *rest) for x, y, *rest in spots),
            rings=tuple((x - dx, y - dy, d) for x, y, d in rings),
            images=tuple((x - dx, y - dy, w, h, jpeg) for x, y, w, h, jpeg in images),
            line_color=_color(coordinator, "slat_lines"),
            marker_color=_color(coordinator, "laser_target"),
            spot_color=_color(coordinator, "red_dots"),
        )
    if camera_name != "overview":
        return {"view": view, "overlay": overlay}
    mode, value = _thickness_or_distance(coordinator)
    surface = p2_geometry.surface_height(mode, value) - calibration.plane_mm
    camera_x, camera_y, camera_height = p2_geometry.OVERVIEW_CAMERA
    lift = SurfaceLift(camera_x, camera_y, camera_height, surface) if surface else None
    return {"view": view, "lift": lift, "overlay": overlay, "lens": p2_geometry.OVERVIEW_LENS}


def p2_view_needs_head(coordinator: XtoolCoordinator, camera_name: str) -> bool:
    """Whether the next picture depends on where the head is."""
    settings = _p2_view(coordinator)

    def on(overlay: str) -> bool:
        return bool(settings[_overlay_key(camera_name, overlay)])

    if on("laser_target") or on("red_dot_close"):
        return True
    if camera_name != "overview":
        # The close camera's picture moves with the head
        return any(on(name) for name in P2_OVERLAYS[camera_name])
    return settings["crop_preset"] in CLOSE_CAMERA_AREAS or on(CLOSE_CAMERA_IMAGE)


async def _refresh(coordinator: XtoolCoordinator, camera_name: str) -> None:
    camera = _thumbnail_registry(coordinator).cameras.get(camera_name)
    if camera is not None:
        await camera.refresh_picture()


class _P2ViewSetting(_CameraDeviceEntity):
    """A P2 camera view setting, kept by Home Assistant (not the laser)."""

    def __init__(self, coordinator: XtoolCoordinator, camera_name: str, name: str) -> None:
        super().__init__(coordinator)
        self._camera_name = camera_name
        self._key = _key(camera_name, name)
        self._set_unique_id(f"camera_{camera_name}_{self._key}")
        self._use_camera_device(camera_name)
        self._attr_translation_key = name
        self._attr_entity_category = (
            None if name in P2_CONTROL_SETTINGS else EntityCategory.CONFIG
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def _value(self) -> Any:
        return _p2_view(self.coordinator)[self._key]

    async def _store(self, value: Any) -> None:
        _p2_view(self.coordinator)[self._key] = value
        self.async_write_ha_state()
        await _refresh(self.coordinator, self._camera_name)


class P2ViewSelect(_P2ViewSetting, SelectEntity, RestoreEntity):
    """A camera's crop preset, or the processing mode."""

    def __init__(
        self, coordinator: XtoolCoordinator, camera_name: str, name: str, options: list[str],
    ) -> None:
        super().__init__(coordinator, camera_name, name)
        self._attr_options = options

    @property
    def current_option(self) -> str:
        return self._value

    async def async_select_option(self, option: str) -> None:
        settings = _p2_view(self.coordinator)
        if self._key == _key(self._camera_name, "crop_preset"):
            calibration = _calibration_of(self.coordinator, self._camera_name)
            preset = None if calibration is None else _preset_view(
                self.coordinator, self._camera_name, option, calibration,
            )
            if preset is not None:
                _show_crop(self.coordinator, self._camera_name, preset, calibration)
        if self._key == "processing_mode":
            # Thickness or distance takes the new mode's range, and the
            # measured value its meaning
            settings[self._key] = option
            settings["thickness_or_distance"] = _thickness_or_distance(self.coordinator)[1]
            self.coordinator.async_update_listeners()
        await self._store(option)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            _p2_view(self.coordinator)[self._key] = last.state


class P2ViewSwitch(_P2ViewSetting, SwitchEntity, RestoreEntity):
    """One overlay on a camera's picture (any number may be on)."""

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


class P2ThicknessOrDistance(_P2ViewSetting, RestoreNumber):
    """The material setting Studio asks for in the selected Mode.

    Process on slats: the material's thickness. Honeycomb panel and Slats
    removed: Studio's distance (25 mm above the slats minus the material's
    top). The range is the mode's; the overview picture shows the
    material's top to scale. Measure thickness or distance fills it in;
    it can be changed after.
    """

    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "overview", "thickness_or_distance")

    @property
    def native_min_value(self) -> float:
        return p2_geometry.MODES[_p2_view(self.coordinator)["processing_mode"]][0]

    @property
    def native_max_value(self) -> float:
        return p2_geometry.MODES[_p2_view(self.coordinator)["processing_mode"]][1]

    @property
    def native_value(self) -> float:
        return _thickness_or_distance(self.coordinator)[1]

    async def async_set_native_value(self, value: float) -> None:
        await self._store(min(max(float(value), self.native_min_value), self.native_max_value))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            # Kept as it was: the Mode may be restored after this number
            _p2_view(self.coordinator)[self._key] = float(last.native_value)


class P2CropNumber(_WSV2CameraViewNumber):
    """A P2 camera's crop number: a preset fills it in, and editing it
    switches Crop to Custom (keeping the other numbers)."""

    _attr_entity_category = None

    async def async_set_native_value(self, value: float) -> None:
        settings = _p2_view(self.coordinator)
        key = _key(self._camera_name, "crop_preset")
        if settings[key] != VIEW_CUSTOM:
            settings[key] = VIEW_CUSTOM
            self.coordinator.async_update_listeners()
        await super().async_set_native_value(value)


def _view_cameras(coordinator: XtoolCoordinator) -> list[str]:
    return [name for name in P2_VIEW_CAMERAS if name in coordinator.model.camera_names]


def build_p2_view_selects(coordinator: XtoolCoordinator) -> list[SelectEntity]:
    entities: list[SelectEntity] = []
    for name in _view_cameras(coordinator):
        entities.append(P2ViewSelect(coordinator, name, "crop_preset", P2_CROP_OPTIONS[name]))
        if name == "overview":
            entities.append(
                P2ViewSelect(coordinator, name, "processing_mode", P2_MODE_OPTIONS),
            )
    return entities


def build_p2_view_switches(coordinator: XtoolCoordinator) -> list[SwitchEntity]:
    return [
        P2ViewSwitch(coordinator, name, f"overlay_{overlay}")
        for name in _view_cameras(coordinator)
        for overlay in P2_OVERLAYS[name]
    ]


def build_p2_view_numbers(coordinator: XtoolCoordinator) -> list[NumberEntity]:
    if "overview" not in coordinator.model.camera_names:
        return []
    return [P2ThicknessOrDistance(coordinator)]


# --- Laser head ------------------------------------------------------------

# The head moves only while the P2 waits for work, as in Studio
# The P2 moves the head while it sleeps too (it homes when it wakes)
P2_HEAD_MOVE_STATUSES = (XtoolStatus.IDLE, XtoolStatus.FINISHED, XtoolStatus.SLEEPING)
# Just after the close red dot turns on, the sensor has not read yet
P2_MEASURE_TRIES = 20
P2_MEASURE_RETRY_SECONDS = 0.1


def _head_lock(coordinator: XtoolCoordinator) -> asyncio.Lock:
    """One move at a time: a move asked for during another waits for it."""
    lock = getattr(coordinator, "_p2_head_lock", None)
    if lock is None:
        lock = coordinator._p2_head_lock = asyncio.Lock()
    return lock


def _head_targets(coordinator: XtoolCoordinator) -> dict[str, float]:
    """Laser position X and Y as set, waiting for Move laser."""
    targets = getattr(coordinator, "_p2_head_targets", None)
    if targets is None:
        targets = coordinator._p2_head_targets = {}
    return targets


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
                f"Laser head not moved: the laser is {status or 'not connected'}, "
                "not idle or sleeping"
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
        # Any move leaves no position waiting
        _head_targets(coordinator).clear()
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
    """Where the laser head is, in mm from home (X to the right, Y toward
    the front).

    Setting it does not move the head: the value waits (and shows) until
    Move laser moves there, so X and Y can both be set first. The
    ``xtool.move`` action moves at once, also in steps.
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

    def _position(self) -> float | None:
        d = self.coordinator.data
        return getattr(d, f"position_{self._axis}", None) if d else None

    @property
    def native_value(self) -> float | None:
        target = _head_targets(self.coordinator).get(self._axis)
        return target if target is not None else self._position()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """``waiting``: set, not moved there yet; ``position``: where the head is."""
        return {
            "waiting": self._axis in _head_targets(self.coordinator),
            "position": self._position(),
        }

    async def async_set_native_value(self, value: float) -> None:
        _head_targets(self.coordinator)[self._axis] = round(float(value), 2)
        # Both axes show it (the other may wait too)
        self.coordinator.async_update_listeners()


class P2MoveLaser(XtoolEntity, ButtonEntity):
    """Moves the head to Laser position X and Y as set (an axis not set
    stays where it is)."""

    _attr_translation_key = "move_laser"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("move_laser")

    async def async_press(self) -> None:
        targets = dict(_head_targets(self.coordinator))
        if not targets:
            raise HomeAssistantError(
                "Laser head not moved: set Laser position X or Y first"
            )
        await async_move_laser_head(self.coordinator, x=targets.get("x"), y=targets.get("y"))


def p2_can_move(coordinator: Any) -> bool:
    """Whether a coordinator's laser is a P2 (its head can be moved)."""
    from .p2 import P2Protocol

    return isinstance(getattr(coordinator, "protocol", None), P2Protocol)


class P2MeasureDistance(XtoolMeasureDistance):
    """Reads the material under the IR sensor, beside the laser.

    The thickness is the P2's own reading at thickness 0 (its
    ``laserMeasureOffset`` setting) minus the distance read. The result,
    as the selected Mode takes it, goes into Thickness or distance (which
    can be changed after).
    """

    _attr_translation_key = "measure_thickness_or_distance"

    async def async_press(self) -> None:
        protocol = self.coordinator.protocol
        data = self.coordinator.data
        if data is None:
            raise HomeAssistantError("The laser did not report a distance")
        # The sensor reads only while the close red dot is on (otherwise
        # the thickness is always 0); it is turned off again after
        dot_was_on = bool(getattr(data, "ir_led_close", False))
        offset = protocol.measure_offset(data)
        if not dot_was_on:
            await protocol.set_ir_led(1, True)
        try:
            reading = await self._read(offset.get("z") if offset else None)
        finally:
            if not dot_was_on:
                await protocol.set_ir_led(1, False)
        # The P2 replies with the thickness (see measure_material)
        thickness = round(reading["distance"], 2)
        data.measured_thickness_mm = thickness
        if offset:
            data.last_distance_mm = round(offset["z"] - thickness, 2)
        dx, dy = (offset["x"], offset["y"]) if offset else p2_geometry.MEASURE_OFFSET
        if "coord_x" in reading and "coord_y" in reading:
            data.measured_at = (round(reading["coord_x"] + dx, 2), round(reading["coord_y"] + dy, 2))
        # Thickness or distance takes it, as the Mode does (and can be changed after)
        settings = _p2_view(self.coordinator)
        mode = settings["processing_mode"]
        low, high = p2_geometry.MODES[mode]
        value = round(p2_geometry.mode_value(mode, thickness), 1)
        settings["thickness_or_distance"] = min(max(value, low), high)
        self.coordinator.async_set_updated_data(data)
        await _refresh(self.coordinator, "overview")


    async def _read(self, zero: float | None) -> dict[str, float]:
        """A reading the sensor made.

        For about 0.2 s after the red dot turns on, the reply is the
        reading at thickness 0 (``zero``, Z): the sensor has not read yet.
        Such a reply is asked again.
        """
        for _ in range(P2_MEASURE_TRIES):
            reading = await self.coordinator.protocol.measure_material()
            if reading is None:
                raise HomeAssistantError("The laser did not report a distance")
            if zero is None or abs(reading["distance"] - zero) > 0.05:
                return reading
            await asyncio.sleep(P2_MEASURE_RETRY_SECONDS)
        raise HomeAssistantError(
            "The IR sensor did not read: is the material under the close red dot?"
        )


class P2Measured(XtoolRestoringSensor, SensorEntity):
    """The last Measure distance, as Studio enters it in the selected Mode.

    Process on slats: the material's thickness (its top above the
    slats). Honeycomb panel and Slats removed: Studio's distance (how far
    the top is below 25 mm above the slats). Attributes: the thickness,
    where on the bed it was measured (the IR sensor's position, mm from
    home) and the distance the sensor read.
    """

    # No distance device class: Home Assistant would show it in inches
    # with US units, and Studio's settings are in mm
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_suggested_display_precision = 1
    _restored_thickness: float | None = None

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "measured_thickness_or_distance"
        self._set_unique_id("measured_thickness_or_distance")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        thickness = last.attributes.get("thickness") if last is not None else None
        if isinstance(thickness, (int, float)):
            self._restored_thickness = float(thickness)

    def _has_restored_value(self) -> bool:
        return self._restored_thickness is not None

    def _thickness(self) -> float | None:
        d = self.coordinator.data
        live = d.measured_thickness_mm if d is not None else None
        return live if live is not None else self._restored_thickness

    @property
    def native_value(self) -> float | None:
        thickness = self._thickness()
        if thickness is None:
            return None
        mode = _p2_view(self.coordinator)["processing_mode"]
        return round(p2_geometry.mode_value(mode, thickness), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        thickness = self._thickness()
        if thickness is None:
            return None
        attributes: dict[str, Any] = {"thickness": thickness}
        d = self.coordinator.data
        if d is not None and d.measured_thickness_mm is not None:
            attributes["sensor_distance"] = d.last_distance_mm
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
            P2CropNumber(coordinator, *setting, camera_name=name)
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


class P2Wake(XtoolEntity, ButtonEntity):
    """Wakes a sleeping P2, so its cameras take photos again.

    The P2 has no wake command; a photo wakes it (Home Assistant takes
    none while it sleeps, so as not to keep it awake).
    """

    _attr_translation_key = "wake"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("wake")

    async def async_press(self) -> None:
        camera_name = self.coordinator.model.camera_names[0]
        try:
            await self.coordinator.protocol.camera_snap(camera_name)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"The P2 did not answer: {err}") from err
        await self.coordinator.async_request_refresh()


def build_p2_buttons(coordinator: XtoolCoordinator) -> list[ButtonEntity]:
    return [
        XtoolHomeLaserHead(coordinator),
        P2Wake(coordinator),
        P2MoveLaser(coordinator),
        P2MeasureDistance(coordinator),
    ]
