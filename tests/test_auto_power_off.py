"""Auto power-off turns the linked Power switch off after Idle or Sleeping.

The count starts when the laser becomes Idle or Sleeping and keeps going
while it goes between the two. Any other state, the laser going offline,
or the Power switch turning off, stops it. Changing the minutes starts
the count again. 0 means never.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from .conftest import POWER_SWITCH, build_coordinator


@pytest.fixture
def setup(hass: HomeAssistant, config_entry):
    from custom_components.xtool.const import XtoolStatus
    from custom_components.xtool.number import XtoolAutoPowerOff
    from custom_components.xtool.protocols import DEVICE_MODELS
    from custom_components.xtool.protocols.base import XtoolDeviceState

    model = next(m for m in DEVICE_MODELS.values() if m.model_id == "F2UltraUV" and m.protocol_version == "V2")
    coordinator = build_coordinator(hass, model)
    coordinator.data = XtoolDeviceState(available=True, status=XtoolStatus.OFF)
    hass.states.async_set(POWER_SWITCH, "on")
    calls = async_mock_service(hass, "homeassistant", "turn_off")
    number = XtoolAutoPowerOff(coordinator)
    number.hass = hass
    number.entity_id = "number.xtool_auto_power_off"
    number.async_write_ha_state = lambda: None

    def status(value):
        coordinator.data.status = value
        number._follow_status()

    return number, status, calls, XtoolStatus


async def _after(hass: HomeAssistant, freezer, minutes: float) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_idle_then_sleeping_keeps_the_count(hass, freezer, setup) -> None:
    number, status, calls, S = setup
    await number.async_set_native_value(10)
    status(S.IDLE)
    await _after(hass, freezer, 6)
    status(S.SLEEPING)
    await _after(hass, freezer, 3)
    assert not calls
    status(S.IDLE)
    await _after(hass, freezer, 1.1)
    assert len(calls) == 1 and calls[0].data == {"entity_id": POWER_SWITCH}


async def test_a_job_restarts_the_count(hass, freezer, setup) -> None:
    number, status, calls, S = setup
    await number.async_set_native_value(10)
    status(S.IDLE)
    await _after(hass, freezer, 8)
    status(S.PROCESSING)
    await _after(hass, freezer, 5)
    status(S.FINISHED)
    status(S.IDLE)
    await _after(hass, freezer, 9)
    assert not calls
    await _after(hass, freezer, 1.1)
    assert len(calls) == 1


async def test_offline_stops_the_count(hass, freezer, setup) -> None:
    number, status, calls, S = setup
    await number.async_set_native_value(5)
    status(S.IDLE)
    number.coordinator.data.available = False
    number._follow_status()
    await _after(hass, freezer, 6)
    assert not calls


async def test_zero_is_never(hass, freezer, setup) -> None:
    number, status, calls, S = setup
    await number.async_set_native_value(0)
    status(S.IDLE)
    await _after(hass, freezer, 2000)
    assert not calls


async def test_changing_the_minutes_counts_from_the_change(hass, freezer, setup) -> None:
    number, status, calls, S = setup
    await number.async_set_native_value(30)
    status(S.IDLE)
    await _after(hass, freezer, 20)
    await number.async_set_native_value(15)
    await _after(hass, freezer, 14)
    assert not calls
    await _after(hass, freezer, 1.1)
    assert len(calls) == 1


async def test_changing_the_minutes_while_not_idle_waits_for_idle(hass, freezer, setup) -> None:
    number, status, calls, S = setup
    status(S.PROCESSING)
    await number.async_set_native_value(5)
    await _after(hass, freezer, 10)
    assert not calls
    status(S.IDLE)
    await _after(hass, freezer, 5.1)
    assert len(calls) == 1


async def test_only_with_a_power_switch(hass, config_entry) -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "custom_components" / "xtool" / "number.py").read_text(encoding="utf-8")
    assert "if coordinator.power_switch_entity_id:\n        entities.append(XtoolAutoPowerOff(coordinator))" in text
