"""The P2's entities, named and written the way xTool Studio does.

Settings are Studio's ``/config/set`` keys (Studio's Device settings for
the P2). Each fill light is its own light. Entities for P2 endpoints that
refuse or ignore the request are not created (see docs/PROTOCOL.md).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime

from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity
from .entities import (
    XtoolCameraExposure,
    XtoolDigitalLock,
    XtoolHomeLaserHead,
    XtoolIRLED,
    XtoolMeasureDistance,
    XtoolReboot,
    _RestCoverSensor,
    _RestPushBinary,
    _fill_light_auto_off,
    _ir_light_auto_off,
    _sleep_timeout,
    _sleep_timeout_open_gap,
)
from .protocol import REST_BRIGHTNESS_MAX

# The P2's fill lights by /peripheral/fill_light index
P2_FILL_LIGHTS = (1, 2, 3)


def _renamed(entity: Any, translation_key: str) -> Any:
    """The entity under Studio's name (its unique ID stays)."""
    entity._attr_translation_key = translation_key
    return entity


# --- Lights ----------------------------------------------------------------


class P2FillLight(XtoolEntity, LightEntity):
    """One of the P2's fill lights (``/peripheral/fill_light`` index 1-3)."""

    _attr_icon = "mdi:track-light"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: XtoolCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._set_unique_id(f"fill_light_{index}")
        self._attr_translation_key = f"fill_light_{index}"

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
        await self.coordinator.protocol.set_fill_light_level(self._index, level)
        data = self.coordinator.data
        if data is not None:
            data.fill_light_levels = {**data.fill_light_levels, self._index: level}
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(int(kwargs.get(ATTR_BRIGHTNESS, REST_BRIGHTNESS_MAX)))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(0)


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
        if not await self.coordinator.protocol.set_config(**kv):
            return
        data = self.coordinator.data
        if data is not None:
            data.device_config = {**data.device_config, **kv}
        self.async_write_ha_state()


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
        super().__init__(coordinator, "smoking_fan", "smoking_fan")

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


class P2FillLightBrightness(_P2Setting, NumberEntity):
    """Studio: Fill light brightness, 0-100 % (stored as 0-255)."""

    _config_key = "fillLightBrightness"
    _attr_icon = "mdi:brightness-percent"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator, "fill_light_brightness", "fill_light_brightness")

    @property
    def native_value(self) -> float | None:
        value = self._value()
        return None if value is None else round(float(value) / REST_BRIGHTNESS_MAX * 100)

    async def async_set_native_value(self, value: float) -> None:
        await self._write(fillLightBrightness=round(value / 100 * REST_BRIGHTNESS_MAX))


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


# --- Builders ----------------------------------------------------------------


def build_p2_switches(coordinator: XtoolCoordinator) -> list[SwitchEntity]:
    return [
        _renamed(XtoolIRLED(coordinator, "close"), "aimed_measure_light"),
        _renamed(XtoolIRLED(coordinator, "global"), "quick_measure_light"),
        _renamed(XtoolDigitalLock(coordinator), "safety_lock"),
        P2Buzzer(coordinator),
        P2ExhaustFan(coordinator),
    ]


def build_p2_numbers(coordinator: XtoolCoordinator) -> list[NumberEntity]:
    return [
        XtoolCameraExposure(coordinator, "overview"),
        XtoolCameraExposure(coordinator, "closeup"),
        _sleep_timeout(coordinator),
        _sleep_timeout_open_gap(coordinator),
        _fill_light_auto_off(coordinator),
        _ir_light_auto_off(coordinator),
        P2ExhaustTime(coordinator),
        P2FillLightBrightness(coordinator),
        P2LowestOutputPower(coordinator),
    ]


def build_p2_selects(coordinator: XtoolCoordinator) -> list[SelectEntity]:
    return [P2FlameAlarm(coordinator)]


def build_p2_lights(coordinator: XtoolCoordinator) -> list[LightEntity]:
    return [P2FillLight(coordinator, index) for index in P2_FILL_LIGHTS]


def build_p2_binary_sensors(coordinator: XtoolCoordinator) -> list[BinarySensorEntity]:
    return [
        _renamed(_RestCoverSensor(coordinator), "lid"),
        P2Baseplate(coordinator),
        _RestPushBinary(coordinator, "cooling_fan_running", "cooling_fan_running",
                        BinarySensorDeviceClass.RUNNING),
        _RestPushBinary(coordinator, "smoking_fan_running", "smoking_fan_running",
                        BinarySensorDeviceClass.RUNNING),
        _RestPushBinary(coordinator, "water_pump_running", "water_pump_running",
                        BinarySensorDeviceClass.RUNNING),
    ]


def build_p2_buttons(coordinator: XtoolCoordinator) -> list[ButtonEntity]:
    return [
        XtoolHomeLaserHead(coordinator),
        XtoolMeasureDistance(coordinator),
        XtoolReboot(coordinator),
    ]
