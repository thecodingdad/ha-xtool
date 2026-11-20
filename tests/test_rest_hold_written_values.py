"""REST lasers (the P2) show a written value at once and keep it until the laser reports it."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import has, requires

pytestmark = requires("rest_hold_written_values")


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest import XTOOL_P2

    coord = build_coordinator(hass, XTOOL_P2)
    coord.protocol.set_config = AsyncMock(return_value=True)
    coord.protocol.set_fill_light_level = AsyncMock()
    coord.protocol.set_digital_lock = AsyncMock()
    state = XtoolDeviceState()
    state.available = True
    state.device_config = {"beepEnable": False, "purifierEnable": False, "purifierTimeout": 10}
    state.fill_light_levels = {1: 0, 2: 255, 3: 255}
    state.cover_locked = False
    coord.data = state
    return coord


def by_key(entities):
    for entity in entities:
        entity.async_write_ha_state = MagicMock()
    # On the close camera's device the close fill light is just "Fill light"
    # The P2's Exhaust fan setting has its own key for its icon
    renamed = {"fill_light": "fill_light_1", "exhaust_fan_setting": "smoking_fan"}
    return {renamed.get(e.translation_key, e.translation_key): e for e in entities}


def poll_with(coordinator, **fields):
    """A poll that began before the write: it still carries the old values."""
    from dataclasses import replace

    state = replace(coordinator.data)
    for name, value in fields.items():
        setattr(state, name, value)
    coordinator.data = state


async def test_setting_held_against_an_older_read(coordinator) -> None:
    switches = by_key(coordinator.build_switches())
    await switches["buzzer"].async_turn_on()
    assert switches["buzzer"].is_on is True
    poll_with(coordinator, device_config={"beepEnable": False, "purifierEnable": False, "purifierTimeout": 10})
    assert switches["buzzer"].is_on is True


async def test_refused_setting_shows_the_old_value(coordinator) -> None:
    coordinator.protocol.set_config = AsyncMock(return_value=False)
    switches = by_key(coordinator.build_switches())
    with pytest.raises(HomeAssistantError):
        await switches["buzzer"].async_turn_on()
    assert switches["buzzer"].is_on is False


async def test_exhaust_fan_holds_both_of_studios_writes(coordinator) -> None:
    switches = by_key(coordinator.build_switches())
    await switches["smoking_fan"].async_turn_on()
    poll_with(coordinator, device_config={"beepEnable": False, "purifierEnable": False, "purifierTimeout": 10})
    assert coordinator.data.device_config["purifierEnable"] is True
    assert coordinator.data.device_config["purifierTimeout"] == 5


async def test_fill_light_held_and_restored_on_failure(coordinator) -> None:
    lights = by_key(coordinator.build_lights())
    await lights["fill_light_1"].async_turn_on(brightness=200)
    poll_with(coordinator, fill_light_levels={1: 0, 2: 255, 3: 255})
    assert lights["fill_light_1"].brightness == 200
    coordinator.protocol.set_fill_light_level = AsyncMock(side_effect=ConnectionError("no"))
    with pytest.raises(ConnectionError):
        await lights["fill_light_2"].async_turn_off()
    assert lights["fill_light_2"].brightness == 255


async def test_safety_lock_held(coordinator) -> None:
    if has("p2_controls"):
        lock = by_key(coordinator.build_locks())["safety_lock"]
        await lock.async_lock()
        poll_with(coordinator, cover_locked=False)
        assert lock.is_locked is True
    else:
        switches = by_key(coordinator.build_switches())
        await switches["safety_lock"].async_turn_on()
        poll_with(coordinator, cover_locked=False)
        assert switches["safety_lock"].is_on is True
    coordinator.protocol.set_digital_lock.assert_awaited_once_with(True)


async def test_fill_light_refusal_raises(monkeypatch) -> None:
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("127.0.0.1")
    monkeypatch.setattr(proto, "_post_json", AsyncMock(return_value={"code": 1, "msg": "failed"}))
    with pytest.raises(ConnectionError):
        await proto.set_fill_light_level(1, 10)
    monkeypatch.setattr(proto, "_post_json", AsyncMock(return_value=0))
    await proto.set_fill_light_level(1, 0)
