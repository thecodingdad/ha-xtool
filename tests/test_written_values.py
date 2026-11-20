"""Tests that a value written from Home Assistant shows at once and stays.

On an F2 Ultra UV the red dot went off, then on 24 ms later: a poll that
began before the write finished after it, with the old value. The
coordinator now holds the old value back until the laser reports the new
one, a change made elsewhere arrives, or the time runs out.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from .conftest import build_coordinator
from .features import requires

pytestmark = requires("written_values_held")

IF2 = "DuctFanV3:SN1"


def _f2_ultra_uv():
    from custom_components.xtool.protocols import DEVICE_MODELS

    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )


def _state(**fields):
    from custom_components.xtool.protocols.base import AccessoryState, XtoolDeviceState

    state = XtoolDeviceState()
    state.available = True
    for name, value in fields.items():
        setattr(state, name, value)
    state.connected_accessories = {
        IF2: AccessoryState(type_id="DuctFanV3", sn="SN1", fields={"buzzer_enable": False}),
    }
    return state


@pytest.fixture
def coordinator(hass: HomeAssistant):
    coordinator = build_coordinator(hass, _f2_ultra_uv())
    coordinator.data = _state(ir_led_global=True, display_brightness=50)
    return coordinator


async def _done() -> dict:
    return {}


async def test_shown_while_sent_and_the_old_value_held(coordinator) -> None:
    sent = asyncio.Event()

    async def send() -> dict:
        await sent.wait()
        return {}

    task = asyncio.create_task(coordinator.async_write({"ir_led_global": False}, send()))
    await asyncio.sleep(0)
    assert coordinator.data.ir_led_global is False, "shown while it is sent"
    coordinator.data = _state(ir_led_global=True)  # a poll begun before the write
    assert coordinator.data.ir_led_global is False
    sent.set()
    await task
    coordinator.data = _state(ir_led_global=True)
    assert coordinator.data.ir_led_global is False, "held after it is sent"
    coordinator.data = _state(ir_led_global=False)  # the laser reports it
    coordinator.data = _state(ir_led_global=True)  # then a change made elsewhere
    assert coordinator.data.ir_led_global is True


async def test_a_change_made_elsewhere_shows(coordinator) -> None:
    await coordinator.async_write({"display_brightness": 80}, _done())
    coordinator.data = _state(display_brightness=30)
    assert coordinator.data.display_brightness == 30


async def test_the_old_value_returns_after_the_time(coordinator, monkeypatch) -> None:
    from custom_components.xtool import coordinator as module

    monkeypatch.setattr(module, "HOLD_SECONDS", 0)
    await coordinator.async_write({"ir_led_global": False}, _done())
    coordinator.data = _state(ir_led_global=True)
    assert coordinator.data.ir_led_global is True, "the laser did not take it"


async def test_a_failed_write_shows_the_value_before(coordinator) -> None:
    async def fail() -> dict:
        raise RuntimeError("refused")

    with pytest.raises(RuntimeError):
        await coordinator.async_write({"ir_led_global": False}, fail())
    assert coordinator.data.ir_led_global is True
    coordinator.data = _state(ir_led_global=True)
    assert coordinator.data.ir_led_global is True


async def test_accessory_values_are_held(coordinator) -> None:
    await coordinator.async_write({"buzzer_enable": True}, _done(), accessory=IF2)
    assert coordinator.data.connected_accessories[IF2].fields["buzzer_enable"] is True
    coordinator.data = _state()  # the IF2 poll before it took the write
    assert coordinator.data.connected_accessories[IF2].fields["buzzer_enable"] is True


async def test_the_if2_buzzer_shows_at_once(hass: HomeAssistant, coordinator) -> None:
    from custom_components.xtool.protocols.accessories.entities import build_accessory_entities

    coordinator.protocol.parts_control = AsyncMock(return_value="ok")
    buzzer = next(
        e for e in build_accessory_entities(coordinator, coordinator.data.connected_accessories[IF2])
        if getattr(e, "_xtool_platform", None) == "switch"
    )
    buzzer.async_write_ha_state = lambda: None
    assert buzzer.is_on is False
    await buzzer.async_turn_on()
    assert buzzer.is_on is True
    coordinator.data = _state()
    assert buzzer.is_on is True


async def test_a_fill_light_turns_on_at_its_last_brightness(coordinator) -> None:
    from custom_components.xtool.protocols.ws_v2.entities import WSV2FillLightFront

    coordinator.protocol.set_config = AsyncMock(return_value={})
    light = WSV2FillLightFront(coordinator)
    light.async_write_ha_state = lambda: None
    coordinator.data = replace(coordinator.data, fill_light_a=120)
    light._handle_coordinator_update()
    await light.async_turn_off()
    assert coordinator.protocol.set_config.await_args.args == ("fillLightBrightFront", 0)
    assert light.is_on is False
    await light.async_turn_on()
    assert coordinator.protocol.set_config.await_args.args == ("fillLightBrightFront", 120)
    assert light.is_on is True


async def test_a_new_fill_light_turns_on_full() -> None:
    from custom_components.xtool.protocols.ws_v2.entities import _WSV2FillLightBase

    assert _WSV2FillLightBase._last_on is None


async def test_an_accepted_setting_is_kept() -> None:
    """The settings are read every sixth poll: the laser's answer is kept at once."""
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    proto.request = AsyncMock(return_value={})
    await proto.set_config("autoSleepEnable", False)
    assert proto._latest["auto_sleep_enable"] is False
    await proto.set_config("fillLightBrightBack", 90)
    assert proto._latest["fill_light_b"] == 90
