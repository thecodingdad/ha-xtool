"""Status is Not responding while the Power switch is on and the laser does not answer.

Before, Status showed Off then, although the laser was powered: on
2026-11-16 the P2 stopped answering while its Power switch was on, and
Home Assistant called it Off until it was power-cycled. Off stays for a
Power switch that is off, and for a laser without a Power switch (an
unreachable laser cannot be told apart from one that is off there).
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from .conftest import POWER_SWITCH, build_coordinator
from .common import COMPONENT


def _models():
    from custom_components.xtool.protocols import DEVICE_MODELS

    # One model of each family (coordinator class)
    seen: dict[type, object] = {}
    for model in DEVICE_MODELS.values():
        seen.setdefault(model.coordinator_class, model)
    return list(seen.values())


def _sensor(hass: HomeAssistant, model, power_switch: str | None):
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.sensor import XtoolStatusSensor

    coordinator = build_coordinator(hass, model)
    coordinator.power_switch_entity_id = power_switch
    coordinator.data = XtoolDeviceState(available=False)
    return coordinator, XtoolStatusSensor(coordinator)


@pytest.mark.parametrize("model", _models(), ids=lambda m: m.model_id)
async def test_not_responding_with_the_power_switch_on(hass: HomeAssistant, config_entry, model) -> None:
    hass.states.async_set(POWER_SWITCH, "on")
    coordinator, sensor = _sensor(hass, model, POWER_SWITCH)
    assert not coordinator.starting
    assert sensor.available
    assert sensor.native_value == "not_responding"
    # Also before the first poll
    coordinator.data = None
    assert sensor.native_value == "not_responding"


@pytest.mark.parametrize("model", _models(), ids=lambda m: m.model_id)
async def test_off_with_the_power_switch_off(hass: HomeAssistant, config_entry, model) -> None:
    hass.states.async_set(POWER_SWITCH, "off")
    _, sensor = _sensor(hass, model, POWER_SWITCH)
    assert sensor.native_value == "off"


@pytest.mark.parametrize("model", _models(), ids=lambda m: m.model_id)
async def test_off_without_a_power_switch(hass: HomeAssistant, config_entry, model) -> None:
    _, sensor = _sensor(hass, model, None)
    assert sensor.native_value == "off"


async def test_answering_again_shows_the_laser_status(hass: HomeAssistant, config_entry) -> None:
    from custom_components.xtool.const import XtoolStatus

    hass.states.async_set(POWER_SWITCH, "on")
    coordinator, sensor = _sensor(hass, _models()[0], POWER_SWITCH)
    assert sensor.native_value == "not_responding"
    coordinator.data.available = True
    coordinator.data.status = XtoolStatus.IDLE
    assert sensor.native_value == "idle"


def test_status_options_and_translations() -> None:
    import json

    from custom_components.xtool.const import XtoolStatus

    # The Status sensor's options are every XtoolStatus value
    assert "not_responding" in [s.value for s in XtoolStatus]
    for path in ("translations/en.json",):
        data = json.loads((COMPONENT / path).read_text(encoding="utf-8"))
        assert data["entity"]["sensor"]["status"]["state"]["not_responding"] == "Not responding"
    data = json.loads((COMPONENT / "translations" / "de.json").read_text(encoding="utf-8"))
    assert data["entity"]["sensor"]["status"]["state"]["not_responding"]
