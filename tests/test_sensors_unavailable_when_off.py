"""Readings are unavailable while the laser is off; lifetime totals keep their last value."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .conftest import POWER_SWITCH, build_coordinator
from .features import requires

pytestmark = requires("sensors_unavailable_when_off")

TOTALS = {"working_time", "session_count", "standby_time", "tool_runtime"}


def _entities(hass: HomeAssistant):
    from custom_components.xtool.protocols import DEVICE_MODELS
    from custom_components.xtool.protocols.base import XtoolDeviceState

    for model in DEVICE_MODELS.values():
        coordinator = build_coordinator(hass, model)
        coordinator.data = XtoolDeviceState(available=True)
        coordinator.last_update_success = True
        entities = [*coordinator.build_sensors(), *coordinator.build_binary_sensors()]
        yield coordinator, entities


def test_only_totals_stay_when_the_laser_cannot_be_reached(hass: HomeAssistant, config_entry) -> None:
    hass.states.async_set(POWER_SWITCH, "on")
    seen_totals = set()
    for coordinator, entities in _entities(hass):
        # Some need more than a reachable laser (an accessory, a reading)
        entities = [e for e in entities if e.available]
        assert entities
        coordinator.data.available = False
        for entity in entities:
            assert entity.available is (entity.translation_key in TOTALS), entity.translation_key
            if entity.available:
                seen_totals.add(entity.translation_key)
    assert {"working_time", "session_count", "standby_time"} <= seen_totals


def test_only_totals_stay_when_the_power_switch_is_off(hass: HomeAssistant, config_entry) -> None:
    hass.states.async_set(POWER_SWITCH, "off")
    for _coordinator, entities in _entities(hass):
        for entity in entities:
            assert entity.available is (entity.translation_key in TOTALS), entity.translation_key


def test_status_is_always_available(hass: HomeAssistant, config_entry) -> None:
    from custom_components.xtool.sensor import XtoolStatusSensor

    hass.states.async_set(POWER_SWITCH, "off")
    coordinator, _ = next(_entities(hass))
    sensor = XtoolStatusSensor(coordinator)
    assert sensor.available and sensor.native_value == "off"
