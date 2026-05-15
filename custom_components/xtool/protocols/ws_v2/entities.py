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

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from aiohttp import web
from homeassistant.components.camera import Camera
from homeassistant.components.event import EventDeviceClass, EventEntity
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
from ...entity import XtoolEntity, XtoolReadOnlyEntity, XtoolRestoringBinarySensor
from ...event import XtoolEvent
from ...sensor import XtoolSensor, XtoolSensorEntityDescription
from ...update import XtoolFirmwareUpdate

_LOGGER = logging.getLogger(__name__)

MIN_SNAPSHOT_INTERVAL = timedelta(seconds=30)
# Per-frame poll cadence for the MJPEG live-view entity. Studio's
# ``captureGlobalImage`` route is on-demand only — no firmware-mandated
# rate. Empirically the V2 firmware tolerates ~1 Hz comfortably; faster
# than ~2 Hz queues snaps behind unfinished file_stream transfers.
LIVE_FRAME_INTERVAL = 1.0


# --- Sensors -----------------------------------------------------------

WSV2_SENSOR_DESCRIPTIONS: tuple[XtoolSensorEntityDescription, ...] = (
    XtoolSensorEntityDescription(
        key="task_id",
        translation_key="task_id",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state, _: state.task_id or None,
    ),
    # ``task_name`` / loaded-G-code filename entity intentionally
    # absent — F1V2 firmware decompile shows ``fileName`` string
    # in laserservice but no V2 endpoint or push surfaces it. The
    # only thing ``/v1/processing/progress`` returns is
    # ``{"progress": "%f"}`` (percent only). Restore once a future
    # firmware / log capture confirms a real wire-source.
    # ``working_mode`` diagnostic sensor removed in v2.5.4 — the
    # firmware ``workingMode`` field on F-series V2 carries the
    # ``"HANDLE"`` / ``"NORMAL"`` enum that mirrors the Stops-when-
    # moved switch, which already surfaces the bool. Showing the
    # raw enum string was confusing. REST / D-series still use
    # ``state.working_mode`` for genuine job-mode tracking.
    XtoolSensorEntityDescription(
        key="task_time",
        translation_key="task_time",
        icon="mdi:timer",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda state, _: state.task_time or None,
    ),
    XtoolSensorEntityDescription(
        key="session_count",
        translation_key="session_count",
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda state, _: state.session_count,
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
    (
        XtoolSensorEntityDescription(
            key="last_job_time",
            translation_key="last_job_time",
            icon="mdi:timer-check",
            entity_category=EntityCategory.DIAGNOSTIC,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            value_fn=lambda state, _: state.last_job_time_seconds or None,
        ),
        "has_runtime_stats",
    ),
    (
        XtoolSensorEntityDescription(
            key="working_seconds",
            translation_key="working_time",
            icon="mdi:timer-cog",
            entity_category=EntityCategory.DIAGNOSTIC,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            state_class=SensorStateClass.TOTAL_INCREASING,
            value_fn=lambda state, _: state.working_seconds,
        ),
        "has_runtime_stats",
    ),
    (
        XtoolSensorEntityDescription(
            key="standby_seconds",
            translation_key="standby_time",
            icon="mdi:timer-sand",
            entity_category=EntityCategory.DIAGNOSTIC,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            state_class=SensorStateClass.TOTAL_INCREASING,
            value_fn=lambda state, _: state.standby_seconds,
        ),
        "has_runtime_stats",
    ),
    (
        XtoolSensorEntityDescription(
            key="tool_runtime_seconds",
            translation_key="tool_runtime",
            icon="mdi:tools",
            entity_category=EntityCategory.DIAGNOSTIC,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            state_class=SensorStateClass.TOTAL_INCREASING,
            value_fn=lambda state, _: state.tool_runtime_seconds,
        ),
        "has_runtime_stats",
    ),
    (
        XtoolSensorEntityDescription(
            key="print_tool_type",
            translation_key="print_tool_type",
            icon="mdi:cog",
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda state, _: state.print_tool_type or None,
        ),
        "has_runtime_stats",
    ),
)


# --- Binary sensors ----------------------------------------------------


class _WSV2BoolSensor(XtoolRestoringBinarySensor, BinarySensorEntity):
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
        self._set_unique_id(f"{key}")
        self._attr_translation_key = key
        if device_class is not None:
            self._attr_device_class = device_class
        if entity_category is not None:
            self._attr_entity_category = entity_category

    @property
    def is_on(self) -> bool | None:
        live: bool | None
        if self.coordinator.data is None:
            live = None
        else:
            live = getattr(self.coordinator.data, self._state_attr, None)
        return self._is_on_or_restored(live)


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
        self._set_unique_id(f"{key}")
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


class _WSV2EnumConfigSwitch(XtoolEntity, SwitchEntity):
    """Switch backed by a string-valued config key on ``/v1/device/configs``.

    Maps a binary on/off UI to two firmware enum tokens (e.g.
    ``workingMode`` ↔ ``"HANDLE"`` / ``"NORMAL"`` on F2 Ultra UV's
    Stops-when-moved toggle).
    """

    _attr_entity_category = EntityCategory.CONFIG
    _config_key: str = ""
    _state_attr: str = ""
    _on_value: str = ""
    _off_value: str = ""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        config_key: str,
        state_attr: str,
        on_value: str,
        off_value: str,
        icon: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._set_unique_id(f"{key}")
        self._attr_translation_key = key
        self._config_key = config_key
        self._state_attr = state_attr
        self._on_value = on_value
        self._off_value = off_value
        if icon is not None:
            self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        if d is None:
            return None
        return getattr(d, self._state_attr, None)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_config(
            self._config_key, self._on_value,
        )
        if self.coordinator.data is not None:
            setattr(self.coordinator.data, self._state_attr, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_config(
            self._config_key, self._off_value,
        )
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
        self._set_unique_id(f"{key}")
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
        self._set_unique_id(f"{key}")
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
        self._set_unique_id("display_brightness")

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


# ``WSV2FlameAlarmSelect`` (3-state sensitivity Off/Low/High) removed
# in v2.5.4 — F-series V2 firmware types ``flameAlarm`` strictly as
# boolean (only the writable ``flame_alarm_v2`` switch survives). No
# firmware revision observed so far exposes a sensitivity-level
# select; if one does, re-introduce the entity + state field then.


# ``WSV2FlameLevelSelect`` removed in v2.5.4 — the ``flameLevelHLSelect``
# config key is rejected by F2 Ultra UV firmware ``40.130.021.00.ht2``
# with ``code 1: failed``. No V2-firmware build observed so far accepts
# it, so the entity is dropped entirely rather than gated. The
# ``flame_level_hl`` field on ``XtoolDeviceState`` is kept in case
# future firmware exposes it via a config GET.


# ``WSV2PurifierSpeedSelect`` migrated to the Purifier accessory
# child device — its write path (``ext_purifier`` peripheral) lives
# now in ``protocols/accessories/purifier.py`` as a ``write_action``
# on the Purifier definition's ``speed_select`` entity spec.


# --- Light --------------------------------------------------------------


class _WSV2FillLightBase(XtoolEntity, LightEntity):
    """Base for fill-light entities on V2 firmware.

    F-family V2 firmware exposes ``front`` and ``back`` channels
    independently (``fillLightBrightFront`` / ``fillLightBrightBack``
    in the ``/device/config`` push). Single-channel models keep one
    light; dual-channel models surface one entity per channel and
    each PUT preserves the other channel's previous value.
    """

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    # Subclasses set these.
    _channel: str = ""          # "front" or "back"
    _state_attr: str = ""       # "fill_light_a" / "fill_light_b"
    _other_state_attr: str = "" # "fill_light_b" / "fill_light_a"
    _other_channel: str = ""    # "back" / "front"

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        if d is None:
            return None
        return (getattr(d, self._state_attr) or 0) > 0

    @property
    def brightness(self) -> int | None:
        d = self.coordinator.data
        if d is None:
            return None
        device_value = getattr(d, self._state_attr) or 0
        return int(
            device_value * BRIGHTNESS_HA_MAX / max(BRIGHTNESS_DEVICE_MAX, 1)
        )

    def _other_value(self) -> int:
        d = self.coordinator.data
        if d is None:
            return 0
        return getattr(d, self._other_state_attr) or 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        ha_brightness = kwargs.get(ATTR_BRIGHTNESS, BRIGHTNESS_HA_MAX)
        device_brightness = int(
            ha_brightness * BRIGHTNESS_DEVICE_MAX / BRIGHTNESS_HA_MAX
        )
        body = {
            self._channel: device_brightness,
            self._other_channel: self._other_value(),
        }
        await self.coordinator.protocol.set_peripheral(
            "fill_light", action="set_bri", **body,
        )
        if self.coordinator.data is not None:
            setattr(
                self.coordinator.data, self._state_attr, device_brightness,
            )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        body = {
            self._channel: 0,
            self._other_channel: self._other_value(),
        }
        await self.coordinator.protocol.set_peripheral(
            "fill_light", action="set_bri", **body,
        )
        if self.coordinator.data is not None:
            setattr(self.coordinator.data, self._state_attr, 0)
        self.async_write_ha_state()


class WSV2FillLight(_WSV2FillLightBase):
    """Single fill-light entity — used when the model has only one
    fill-light channel (or where surfacing two entities would be UX
    noise). PUT writes both channels to the same value, mirroring
    Studio's single-slider UX for legacy V2 firmware.
    """

    _attr_translation_key = "fill_light"
    _attr_icon = "mdi:dome-light"
    _channel = "front"
    _state_attr = "fill_light_a"
    _other_state_attr = "fill_light_b"
    _other_channel = "back"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("fill_light")

    async def async_turn_on(self, **kwargs: Any) -> None:
        ha_brightness = kwargs.get(ATTR_BRIGHTNESS, BRIGHTNESS_HA_MAX)
        device_brightness = int(
            ha_brightness * BRIGHTNESS_DEVICE_MAX / BRIGHTNESS_HA_MAX
        )
        await self.coordinator.protocol.set_peripheral(
            "fill_light", action="set_bri",
            front=device_brightness, back=device_brightness,
        )
        if self.coordinator.data is not None:
            self.coordinator.data.fill_light_a = device_brightness
            self.coordinator.data.fill_light_b = device_brightness
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.protocol.set_peripheral(
            "fill_light", action="set_bri", front=0, back=0,
        )
        if self.coordinator.data is not None:
            self.coordinator.data.fill_light_a = 0
            self.coordinator.data.fill_light_b = 0
        self.async_write_ha_state()


class WSV2FillLightFront(_WSV2FillLightBase):
    """Front fill-light channel — F-family V2 firmware."""

    _attr_translation_key = "fill_light_front"
    _attr_icon = "mdi:dome-light"
    _channel = "front"
    _state_attr = "fill_light_a"
    _other_state_attr = "fill_light_b"
    _other_channel = "back"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("fill_light_front")


class WSV2FillLightBack(_WSV2FillLightBase):
    """Back fill-light channel — F-family V2 firmware."""

    _attr_translation_key = "fill_light_back"
    _attr_icon = "mdi:dome-light"
    _channel = "back"
    _state_attr = "fill_light_b"
    _other_state_attr = "fill_light_a"
    _other_channel = "front"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("fill_light_back")


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
        self._set_unique_id(f"{key}")
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
        await self.coordinator.protocol.set_processing_state("pause")


class WSV2ResumeJob(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "resume_job", "mdi:play")

    async def _action(self) -> None:
        # Studio's bundle resumes a paused job by re-issuing
        # ``action=start`` against ``/v1/processing/state``; there is
        # no separate ``resume`` verb.
        await self.coordinator.protocol.set_processing_state("start")


class WSV2CancelJob(_WSV2Button):
    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "cancel_job", "mdi:stop")

    async def _action(self) -> None:
        await self.coordinator.protocol.set_processing_state("stop")


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


class WSV2ZAxisHoming(_WSV2Button):
    """Z-axis homing for F2 Ultra UV (Studio's ``zAxisReset``)."""

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "z_axis_homing", "mdi:axis-z-arrow-lock")

    async def _action(self) -> None:
        # Studio's ``focusControl`` route triggers Z-homing with
        # ``autoHome:1`` plus a safe pre-stop and a 300 mm/min feed.
        # Z value is unused when ``autoHome`` is set but the body
        # field is required.
        await self.coordinator.protocol.request(
            "/v1/laser-head/focus/control",
            "PUT",
            data={
                "action": "goTo",
                "autoHome": 1,
                "stopFirst": 1,
                "F": 300,
                "Z": 0,
            },
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
    """V2 camera — single entity per physical lens.

    Serves both the still-snapshot flow (``async_camera_image``) and
    the live MJPEG preview (``handle_async_mjpeg_stream``) over the
    same ``/v1/camera/snap?name=<n>`` wire path. HA's picture-card
    auto-subscribes to the streaming method when
    ``_attr_is_streaming`` is ``True``, while the
    ``camera.snapshot`` service still consumes the
    snapshot-cached path.

    Previously split into separate ``_WSV2Camera`` +
    ``_WSV2LiveCamera`` entities; dual-camera models then surfaced
    four entries on the device page. Merged in v2.5.4.
    """

    _camera_name: str = ""
    _attr_is_streaming = True

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        camera_name: str,
        icon: str | None = None,
        translation_key: str | None = None,
    ) -> None:
        XtoolEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._set_unique_id(f"{key}")
        self._attr_translation_key = translation_key or key
        self._camera_name = camera_name
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
            image = await self.coordinator.protocol.camera_snap(self._camera_name)
        except Exception as err:
            _LOGGER.debug("V2 camera %s snap failed: %s", self._camera_name, err)
            return self._last_snapshot
        if image:
            self._last_snapshot = image
            self._last_snapshot_time = now
        return self._last_snapshot

    async def handle_async_mjpeg_stream(
        self, request: web.Request,
    ) -> web.StreamResponse | None:
        """Multipart-MJPEG live preview at ``LIVE_FRAME_INTERVAL``.

        Substitute for the unimplemented WebRTC ``media_stream``
        path (see PROTOCOL.md). HA's Lovelace picture-card renders
        the resulting ``multipart/x-mixed-replace`` stream as a
        continuous video feed.
        """
        boundary = "--xtoolframe"
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": (
                    f"multipart/x-mixed-replace; boundary={boundary[2:]}"
                ),
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )
        await response.prepare(request)
        try:
            while True:
                try:
                    jpeg = await self.coordinator.protocol.camera_snap(
                        self._camera_name,
                    )
                except Exception as err:
                    _LOGGER.debug(
                        "V2 live MJPEG %s: snap failed: %s",
                        self._camera_name, err,
                    )
                    jpeg = None
                if jpeg:
                    await response.write(
                        boundary.encode()
                        + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode()
                        + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                await asyncio.sleep(LIVE_FRAME_INTERVAL)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        return response


# --- Firmware update ----------------------------------------------------


class WSV2FirmwareUpdate(XtoolFirmwareUpdate):
    """V2 firmware update — same orchestrator, just a thin name marker."""


# --- Events --------------------------------------------------------------

# V2-specific event-type vocabularies. Job/error sets mostly align
# with the universal XtoolStatus enum but P_EMERGENCY_STOP is V2-only,
# and V2 push events deliver button presses directly (REST polls the
# `last_button_event` peripheral once per cycle, S1/D-series have no
# button push at all — see each family's entities.py for its own list).

WSV2_BUTTON_EVENT_TYPES: tuple[str, ...] = (
    "short_press",
    "long_press",
    "double_press",
)

WSV2_JOB_EVENT_TYPES: tuple[str, ...] = (
    "started",
    "paused",
    "resumed",
    "cancelled",
    "finished",
    "framing_started",
    "framing_finished",
)

WSV2_ERROR_EVENT_TYPES: tuple[str, ...] = (
    "limit",
    "laser_control",
    "laser_module",
    "tilt",
    "moving",
    "emergency_stop",
    "temperature",
    "gyro",
    "laser_head_fault",
    "z_axis_fault",
    "u_axis_fault",
    "conveyor_fault",
    "board_fault",
    "camera_fault",
    "dongle_fault",
    "udisk_fault",
    "machine_lock_md_fault",
)

WSV2_FIRE_WARNING_EVENT_TYPES: tuple[str, ...] = (
    "triggered",
    "cleared",
)


class WSV2ButtonEvent(XtoolEvent):
    """Front-panel button presses (V2 ``/button/status`` push)."""

    _kind = "button"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "button", WSV2_BUTTON_EVENT_TYPES)


class WSV2JobEvent(XtoolEvent):
    """Job-lifecycle transitions derived from Status edges + P_* mode pushes."""

    _kind = "job"
    _attr_icon = "mdi:cog-play"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "job", WSV2_JOB_EVENT_TYPES)


class WSV2ErrorEvent(XtoolEvent):
    """Error transitions — Status enum edges + ``EMERGENCY_STOP`` push."""

    _kind = "error"
    _attr_icon = "mdi:alert-circle"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "error", WSV2_ERROR_EVENT_TYPES)


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
        # ``/peripheral/machine_lock`` reports the USB safety-key
        # presence, *not* a lid lock — Studio's transformResult is
        # ``{state:s} => ({UsbKeyLockStatus: s === "on"})``. Render
        # as a PLUG sensor so HA shows "Plugged in" (key present) /
        # "Unplugged" (key removed). The legacy LOCK device class
        # inverted the polarity and made the entity show "Locked"
        # while the laser was idle with the lid open. The
        # translation key stays ``machine_lock`` so the registered
        # unique_id / entity_id stay stable; the display label is
        # updated to "Safety key" in strings + translations.
        entities.append(
            _bool_sensor_factory(
                "machine_lock", "machine_lock",
                BinarySensorDeviceClass.PLUG,
            )(coordinator)
        )
    # ``air_assist_connected`` migrated to the AirPump / AirPumpV2
    # accessory child device (see ``protocols/accessories/air_pump``).
    # The laser-state ``state.air_assist_enabled`` is merged into the
    # accessory's ``fields["connected"]`` by ``_poll_accessories``.

    if model.has_cooling_fan:
        entities.append(
            _bool_sensor_factory(
                "cooling_fan_running", "cooling_fan_running",
                BinarySensorDeviceClass.RUNNING,
            )(coordinator)
        )
    if model.has_smoking_fan:
        entities.append(
            _bool_sensor_factory(
                "smoking_fan_running", "smoking_fan_running",
                BinarySensorDeviceClass.RUNNING,
            )(coordinator)
        )

    # Always-on binary sensors (V2 baseline)
    entities.append(
        _bool_sensor_factory(
            "alarm_present", "alarm",
            BinarySensorDeviceClass.PROBLEM,
        )(coordinator)
    )
    # ``beep_enabled`` / ``gap_check_enabled`` / ``machine_lock_check_enabled``
    # diagnostic binary-sensors removed in v2.5.4 — the writable config
    # switches ("Beep", "Cover check", "Stops when moved") already
    # expose this state, the diagnostic mirrors were pure duplication.
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

    # Config-backed toggles (always available on V2 firmware).
    # NOTE: flameAlarm is an integer enum (0/1/2 = high/low/off) on
    # firmware — the dedicated `WSV2FlameAlarmSelect` writes it
    # correctly, so we deliberately do not surface it as a boolean
    # switch (would send `true`/`false`, rejected by firmware with
    # `code 1: failed`).
    entities.extend([
        _WSV2ConfigSwitch(
            coordinator, "beep_enable", "beepEnable", "beep_enabled_v2",
            "mdi:volume-high",
        ),
        _WSV2ConfigSwitch(
            coordinator, "gap_check", "gapCheck", "gap_check_enabled",
            "mdi:window-shutter-alert",
        ),
        # ``filter_check`` (config key ``filterCheck``) removed in
        # v2.5.4 — confirmed absent from every xTool Studio bundle
        # and rejected by F2 Ultra UV firmware. Filter-check
        # enforcement is implicit in the Purifier accessory's
        # presence.
        # ``purifier_check`` + ``purifier_continue`` migrated to the
        # Purifier accessory child device; the entity layer surfaces
        # them on the AP2 / AP2-Large / AP2-Max accessory whenever
        # one is currently paired (laser-state values are merged via
        # ``WSV2Coordinator._poll_accessories``).
    ])
    if model.has_machine_lock:
        # F2 Ultra UV (and the rest of the F-series V2 firmware) types
        # the Stops-when-moved enforcement field as the ``workingMode``
        # *enum* — ``"HANDLE"`` = enforcement on, ``"NORMAL"`` = off.
        # The previous ``machineLockCheck`` boolean key is silently
        # ignored by this firmware.
        entities.append(
            _WSV2EnumConfigSwitch(
                coordinator, "stops_when_moved", "workingMode",
                "stops_when_moved",
                on_value="HANDLE", off_value="NORMAL",
                icon="mdi:vibrate",
            )
        )
    if model.has_drawer:
        entities.append(
            _WSV2ConfigSwitch(
                coordinator, "drawer_check", "drawerCheck", "drawer_check",
                "mdi:archive-check",
            )
        )
    if model.has_device_sleep:
        entities.append(
            _WSV2ConfigSwitch(
                coordinator, "device_sleep", "autoSleepEnable",
                "auto_sleep_enable", "mdi:power-sleep",
            )
        )

    # Peripheral-control toggles
    if model.has_cooling_fan:
        entities.append(_WSV2PeripheralSwitch(
            coordinator, "cooling_fan", "cooling_fan", "cooling_fan_running",
            "mdi:fan", SwitchDeviceClass.SWITCH,
        ))
    if model.has_smoking_fan:
        entities.append(_WSV2PeripheralSwitch(
            coordinator, "smoking_fan", "smoking_fan", "smoking_fan_running",
            "mdi:fan-chevron-up", SwitchDeviceClass.SWITCH,
        ))

    if model.has_ir_led:
        # Studio's ``controlRedLed`` peripheral defines two indices
        # (``closeup`` + ``global``), but the F1V2 firmware decompile
        # shows no branching on the ``index`` parameter — both
        # values drive the same physical LED on V2 hardware (the
        # ``global_ir.json`` calibration file confirms a single LED
        # array). Keeping just the ``global`` index avoids surfacing
        # a redundant duplicate entity; if a future model genuinely
        # exposes two LEDs the ``closeup`` variant can be added back.
        # User-facing label is "Red dot" (matches Studio's wording);
        # icon is the laser-pointer crosshair Studio uses too.
        entities.append(
            _WSV2PeripheralSwitch(
                coordinator, "ir_led", "ir_led",
                "ir_led_global", "mdi:laser-pointer",
                SwitchDeviceClass.SWITCH,
                extra={"index": "global"},
            )
        )
    if model.has_digital_lock:
        entities.append(
            _WSV2PeripheralSwitch(
                coordinator, "digital_lock", "digital_lock", "cover_locked",
                "mdi:lock", SwitchDeviceClass.SWITCH,
            )
        )
    return entities


def build_wsv2_numbers(coordinator: XtoolCoordinator) -> list[NumberEntity]:
    # ``air_assist_close_delay`` migrated to the AirPump accessory
    # child device. See ``protocols/accessories/air_pump.py``.
    # ``sleep_timeout`` + ``sleep_timeout_open_gap`` removed —
    # firmware-string audit (F1V2 decompile) shows no
    # ``sleepTimeout`` / ``sleepTimeoutOpenGap`` config-key handler
    # on V2 hardware. Studio displays them but the device no-ops
    # the writes, so the entities surfaced Unknown forever.
    entities: list[NumberEntity] = [
        _WSV2ConfigNumber(
            coordinator, "smoking_fan_duration", "smokingFanDelay",
            "smoking_fan_duration", 1, 600, 1,
            UnitOfTime.SECONDS, "mdi:fan-clock",
        ),
        _WSV2ConfigNumber(
            coordinator, "fire_level", "fireLevel",
            "fire_level", 0, 255, 1,
            None, "mdi:fire",
        ),
    ]
    model = coordinator.model
    # ``air_assist_gear_cut`` + ``air_assist_gear_engrave`` migrated
    # to the AirPump accessory child device (the per-job-mode default
    # gear is conceptually an AirPump setting even though the wire
    # path is the laser's ``/v1/device/configs`` API).
    # ``fill_light_auto_off`` Number removed in v2.5.4 — confirmed
    # absent from every xTool Studio bundle (search for
    # ``fillLightAutoOff`` returns zero hits) and the V2 device
    # ignores writes. Fill-light brightness goes back to 0 manually
    # if needed; no firmware-managed auto-off exists.
    # ``ir_light_auto_off`` config-number removed — the
    # ``irLightAutoOff`` config key is absent from the F1V2
    # firmware decompile; Studio displays it but the V2 device
    # ignores writes.
    # ``purifier_timeout`` migrated to the Purifier accessory child
    # device. The laser-host config value is merged into the AP2
    # accessory's ``fields["purifier_timeout"]`` via
    # ``WSV2Coordinator._poll_accessories``.
    if model.has_camera_exposure:
        # P-family kept the legacy dual exposure config keys
        # (``exposureOverview`` / ``exposureCloseup``). Single-camera
        # V2 firmware doesn't expose these — the exposure is set via
        # ``POST /v1/camera`` directly with no persistent config key.
        # Build the dual entities only when the model is genuinely
        # dual-camera; the single-camera case stays unbuilt until a
        # firmware sample confirms an alternative wire shape.
        if model.camera_names == ("overview", "closeup"):
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
    # ``WSV2FlameAlarmSelect`` removed in v2.5.4 — firmware types
    # ``flameAlarm`` strictly as boolean, the writable switch already
    # covers it. ``WSV2FlameLevelSelect`` removed in v2.5.4 (firmware
    # rejects ``flameLevelHLSelect``). ``WSV2PurifierSpeedSelect``
    # migrated to the Purifier accessory child device — the entity
    # surfaces only when an AP2 / AP2-Large / AP2-Max is currently
    # paired.
    return []


def build_wsv2_lights(coordinator: XtoolCoordinator) -> list[LightEntity]:
    model = coordinator.model
    if model.has_fill_light_dual:
        return [
            WSV2FillLightFront(coordinator),
            WSV2FillLightBack(coordinator),
        ]
    if model.has_fill_light:
        return [WSV2FillLight(coordinator)]
    return []


def build_wsv2_buttons(coordinator: XtoolCoordinator) -> list[ButtonEntity]:
    # NOTE: WSV2Reboot and WSV2SyncTime are deliberately omitted —
    # `reboot` and `setTime` are not real keys on /v1/device/configs
    # for any V2 model in the Studio bundle, and the firmware rejects
    # them with `code 1: failed`. The classes are kept in this module
    # for future use if a real wire path is identified.
    entities: list[ButtonEntity] = [
        WSV2PauseJob(coordinator),
        WSV2ResumeJob(coordinator),
        WSV2CancelJob(coordinator),
    ]
    # XY-home + Move-to-origin only make sense on gantry models. Galvo
    # devices (F2 family, F1 portable …) have no XY motion path and
    # would only see ``code 1: failed`` if the buttons were pressed.
    if coordinator.model.has_laser_head_position:
        entities.append(WSV2HomeXY(coordinator))
        entities.append(WSV2HomeLaser(coordinator))
    if coordinator.model.has_z_axis:
        entities.append(WSV2HomeAll(coordinator))
        entities.append(WSV2HomeZ(coordinator))
    if coordinator.model.has_distance_measure:
        entities.append(WSV2MeasureDistance(coordinator))
    if coordinator.model.has_z_axis_homing:
        entities.append(WSV2ZAxisHoming(coordinator))
    return entities


def build_wsv2_cameras(coordinator: XtoolCoordinator) -> list[Camera]:
    """Build one camera entity per ``model.camera_names`` entry.

    Each entity serves both ``async_camera_image`` (still
    snapshot, cached) and ``handle_async_mjpeg_stream`` (live
    preview). The earlier split into snapshot + live entities
    surfaced four entries on dual-camera devices; collapsed in
    v2.5.4. Skipping when ``camera_names`` is empty avoids
    creating entities whose wire-shape we haven't audited.
    """
    if not coordinator.model.has_camera:
        return []
    names = coordinator.model.camera_names
    if not names:
        return []

    # When the model exposes a single camera the entity-id key stays
    # generic ("camera") and the translated label is just "Camera".
    # Multi-camera models keep the per-name suffix
    # ("camera_main", "camera_deep") so HA shows them distinctly.
    is_single = len(names) == 1
    cameras: list[Camera] = []
    icons = ("mdi:camera", "mdi:camera-burst")
    for idx, name in enumerate(names):
        suffix = "" if is_single else f"_{name}"
        key = f"camera{suffix}"
        cameras.append(
            _WSV2Camera(
                coordinator, key, name,
                icons[min(idx, len(icons) - 1)],
            )
        )
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


class WSV2FireWarningEvent(XtoolEvent):
    """Flame-detector triggered / cleared (V2: ``state.alarm_present`` edge)."""

    _kind = "fire_warning"
    _attr_icon = "mdi:fire-alert"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(
            coordinator, "fire_warning", WSV2_FIRE_WARNING_EVENT_TYPES,
        )


def build_wsv2_events(coordinator: XtoolCoordinator) -> list[EventEntity]:
    entities: list[EventEntity] = [
        WSV2JobEvent(coordinator),
        WSV2ErrorEvent(coordinator),
        WSV2FireWarningEvent(coordinator),
    ]
    if coordinator.model.has_button_event:
        entities.insert(0, WSV2ButtonEvent(coordinator))
    return entities
