"""After the Power switch turns on, Status shows Initializing while the laser starts.

Before, Status showed Off for the whole start, and turning the switch on
republished the last poll from before the power was cut (Idle), so Status
went Idle, Off, Idle.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import POWER_SWITCH, build_coordinator
from .features import COMPONENT, has, requires

pytestmark = requires("power_on_starting")


@pytest.fixture
def setup(hass: HomeAssistant, config_entry):
    from custom_components.xtool.const import XtoolStatus
    from custom_components.xtool.protocols import DEVICE_MODELS
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.sensor import XtoolStatusSensor

    model = next(m for m in DEVICE_MODELS.values() if m.model_id == "P2")
    hass.states.async_set(POWER_SWITCH, "on")
    coordinator = build_coordinator(hass, model)
    coordinator.data = XtoolDeviceState(available=True, status=XtoolStatus.IDLE)
    unsub = coordinator.async_track_power_switch()
    sensor = XtoolStatusSensor(coordinator)
    yield coordinator, sensor
    unsub()


async def test_initializing_after_power_on(hass: HomeAssistant, setup, monkeypatch) -> None:
    from custom_components.xtool import coordinator as coordinator_module

    coordinator, sensor = setup
    assert sensor.native_value == "idle"
    hass.states.async_set(POWER_SWITCH, "off")
    await hass.async_block_till_done()
    assert sensor.native_value == "off"
    # The last poll is from before the power was cut: not shown as Idle again
    assert coordinator.data.available is False
    hass.states.async_set(POWER_SWITCH, "on")
    await hass.async_block_till_done()
    assert sensor.native_value == "initializing"
    # The laser answers
    coordinator.data.available = True
    assert sensor.native_value == "idle"
    # It does not answer: still starting, until the time is over
    coordinator.data.available = False
    assert sensor.native_value == "initializing"
    now = coordinator._powered_on_at
    monkeypatch.setattr(
        coordinator_module.time, "monotonic",
        lambda: now + coordinator_module.POWER_ON_STARTUP_SECONDS + 1,
    )
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=coordinator_module.POWER_ON_STARTUP_SECONDS + 1),
    )
    await hass.async_block_till_done()
    # Powered, but the laser does not answer after its start-up time
    assert sensor.native_value == ("not_responding" if has("status_not_responding") else "off")


@requires("startup_ends_at_first_status")
async def test_lost_after_first_status_is_not_initializing(hass: HomeAssistant, setup) -> None:
    """The laser answered once, then stopped: Status must not go back to Initializing."""
    from custom_components.xtool.const import XtoolStatus
    from custom_components.xtool.protocols.base import XtoolDeviceState

    coordinator, sensor = setup
    hass.states.async_set(POWER_SWITCH, "off")
    await hass.async_block_till_done()
    hass.states.async_set(POWER_SWITCH, "on")
    await hass.async_block_till_done()
    assert coordinator.starting
    # A first answer without a status: still starting
    coordinator.async_set_updated_data(XtoolDeviceState(available=True, status=None))
    assert coordinator.starting
    assert sensor.native_value == "initializing"
    # The first status: started
    coordinator.async_set_updated_data(XtoolDeviceState(available=True, status=XtoolStatus.IDLE))
    assert not coordinator.starting
    assert sensor.native_value == "idle"
    # It stops answering within the start-up time
    coordinator.async_set_updated_data(XtoolDeviceState(available=False))
    assert sensor.native_value == ("not_responding" if has("status_not_responding") else "off")


async def test_off_while_switched_off(hass: HomeAssistant, setup) -> None:
    coordinator, sensor = setup
    hass.states.async_set(POWER_SWITCH, "off")
    await hass.async_block_till_done()
    coordinator.data.available = False
    assert sensor.native_value == "off"
    assert not coordinator.starting


def test_the_coordinator_follows_the_power_switch() -> None:
    text = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    assert "coordinator.async_track_power_switch()" in text
