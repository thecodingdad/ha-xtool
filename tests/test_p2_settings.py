"""The P2's settings and controls, named and written the way xTool Studio does."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator

CONFIG = {
    "beepEnable": False, "fillLightBrightness": 204, "flameAlarm": True, "flameAlarmLevel1": False,
    "flameLevelHLSelect": 1, "motionPowerCut": "M37S3", "purifierEnable": False, "purifierTimeout": 5,
}


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest import XTOOL_P2

    coord = build_coordinator(hass, XTOOL_P2)
    coord.protocol.set_config = AsyncMock(return_value=True)
    coord.protocol.set_fill_light_level = AsyncMock()
    state = XtoolDeviceState()
    state.available = True
    state.device_config = dict(CONFIG)
    state.fill_light_levels = {1: 0, 2: 255, 3: 255}
    coord.data = state
    return coord


def by_key(entities):
    for entity in entities:
        entity.async_write_ha_state = MagicMock()
    # On the close camera's device the close fill light is just "Fill light"
    # The P2's Exhaust fan setting has its own key for its icon
    renamed = {"fill_light": "fill_light_1", "exhaust_fan_setting": "smoking_fan"}
    return {renamed.get(e.translation_key, e.translation_key): e for e in entities}


def test_p2_entities_use_studio_names(coordinator) -> None:
    """The P2 offers what Studio offers, under Studio's names."""
    switches = by_key(coordinator.build_switches())
    expected = {"aimed_measure_light", "quick_measure_light", "buzzer", "smoking_fan"}
    # The Safety lock is a lock
    assert set(by_key(coordinator.build_locks())) == {"safety_lock"}
    # The cameras' overlay switches
    expected |= {
        "overlay_mode_process_on_slats", "overlay_mode_honeycomb_panel",
        "overlay_mode_slats_removed", "overlay_close_camera", "overlay_studio",
        "overlay_custom", "overlay_laser_target", "overlay_slat_lines",
        "overlay_red_dot_close", "overlay_red_dot_quick",
    }
    assert set(switches) == expected
    numbers = by_key(coordinator.build_numbers())
    assert {"smoking_fan_duration", "lowest_output_power"} <= set(numbers)
    assert "fill_light_brightness" not in numbers
    selects = {"flame_alarm_level", "crop_preset", "processing_mode", "overlays"}
    assert set(by_key(coordinator.build_selects())) == selects
    assert set(by_key(coordinator.build_lights())) == {"fill_light_1", "fill_light_2", "fill_light_3"}
    assert {"lid", "baseplate"} <= set(by_key(coordinator.build_binary_sensors()))
    assert "time_sync" not in by_key(coordinator.build_buttons())
    assert "hardware_type" not in {e.translation_key for e in coordinator.build_sensors()}


def test_settings_read_as_studio_shows_them(coordinator) -> None:
    """Fill light brightness in %, Lowest output power from M37S<n>, Flame alarm Low."""
    numbers = by_key(coordinator.build_numbers())
    assert numbers["lowest_output_power"].native_value == 3
    assert numbers["smoking_fan_duration"].native_value == 5
    assert by_key(coordinator.build_selects())["flame_alarm_level"].current_option == "low"
    switches = by_key(coordinator.build_switches())
    assert switches["buzzer"].is_on is False
    assert switches["smoking_fan"].is_on is False


async def test_settings_write_studios_keys(coordinator) -> None:
    """Each setting writes the key Studio writes."""
    set_config = coordinator.protocol.set_config
    numbers = by_key(coordinator.build_numbers())
    await numbers["lowest_output_power"].async_set_native_value(7)
    set_config.assert_awaited_with(motionPowerCut="M37S7")
    await by_key(coordinator.build_selects())["flame_alarm_level"].async_select_option("high")
    set_config.assert_awaited_with(flameLevelHLSelect=1, flameAlarmLevel1=True)
    switches = by_key(coordinator.build_switches())
    await switches["buzzer"].async_turn_on()
    set_config.assert_awaited_with(beepEnable=True)
    set_config.reset_mock()
    await switches["smoking_fan"].async_turn_on()
    assert [c.kwargs for c in set_config.await_args_list] == [{"purifierEnable": True}, {"purifierTimeout": 5}]
    assert coordinator.data.device_config["beepEnable"] is True
    assert coordinator.data.device_config["flameAlarmLevel1"] is True


async def test_a_refused_setting_keeps_the_old_value(coordinator) -> None:
    """When the laser refuses a write, the entity keeps showing the laser's value."""
    coordinator.protocol.set_config = AsyncMock(return_value=False)
    buzzer = by_key(coordinator.build_switches())["buzzer"]
    # Since written values are held, a refusal is also shown as an error
    with contextlib.suppress(HomeAssistantError):
        await buzzer.async_turn_on()
    assert buzzer.is_on is False


async def test_each_fill_light_is_its_own_light(coordinator) -> None:
    """Fill lights 1-3 read and write their own index."""
    lights = by_key(coordinator.build_lights())
    assert lights["fill_light_1"].is_on is False
    assert lights["fill_light_2"].brightness == 255
    await lights["fill_light_1"].async_turn_on(brightness=128)
    coordinator.protocol.set_fill_light_level.assert_awaited_with(1, 128)
    assert lights["fill_light_1"].brightness == 128
    await lights["fill_light_3"].async_turn_off()
    coordinator.protocol.set_fill_light_level.assert_awaited_with(3, 0)
    assert coordinator.data.fill_light_levels == {1: 128, 2: 255, 3: 0}


async def test_config_set_body_is_studios(monkeypatch) -> None:
    """``/config/set`` carries Studio's alias, type and key-value object."""
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("127.0.0.1")
    sent = []

    async def post_json(path, data=None, timeout=5.0):
        sent.append((path, data))
        return {"code": 0, "msg": "Success"} if "beepEnable" in data["kv"] else {"code": 1, "msg": "no"}

    monkeypatch.setattr(proto, "_post_json", post_json)
    assert await proto.set_config(beepEnable=True) is True
    assert sent[0] == ("/config/set", {"alias": "config", "type": "user", "kv": {"beepEnable": True}})
    assert await proto.set_config(nothing=1) is False


async def test_a_fill_light_turns_on_at_studios_brightness(coordinator) -> None:
    """Turned on without a brightness, a fill light uses Studio's Fill light brightness."""
    lights = by_key(coordinator.build_lights())
    await lights["fill_light_1"].async_turn_on()
    coordinator.protocol.set_fill_light_level.assert_awaited_with(1, 204)
    coordinator.data.device_config = {**CONFIG, "fillLightBrightness": 0}
    await lights["fill_light_3"].async_turn_on()
    coordinator.protocol.set_fill_light_level.assert_awaited_with(3, 255)
