"""The xTool M1 Ultra (MLM) on WS-V2 firmware, as a real M1 Ultra behaves.

Built from what an M1 Ultra on firmware 40.41.018.2020.01.ht4 answered
(see m1_ultra_capture.py). It does not push which tools are fitted,
only that a module was unlocked and locked again, so ``workhead_ID``
(and ``knife_head`` while the multi-function carriage is fitted) is
read on every poll while it is awake.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from custom_components.xtool.protocols import DEVICE_MODELS, tool_heads
from custom_components.xtool.protocols.base import XtoolDeviceState

from ..conftest import build_coordinator
from .m1_ultra_capture import (
    KNIFE_HEAD_ROTARY_BLADE,
    SWAP_PUSHES,
    FakeM1Ultra,
)

TOOL_HEAD_SENSORS = {"workhead_id", "basic_carriage", "blade"}


def _model(protocol_version: str):
    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "M1Ultra" and m.protocol_version == protocol_version
    )


def _protocol(laser: FakeM1Ultra):
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    proto._model = _model("V2")
    proto._connected = True
    proto.request = laser
    return proto


def test_flags() -> None:
    for version in ("V1", "V2"):
        model = _model(version)
        assert model.has_tool_heads, version
        assert not model.has_workhead_id, version


async def test_reads_the_fitted_tools() -> None:
    laser = FakeM1Ultra()
    state = XtoolDeviceState()
    await _protocol(laser).poll_state(state)
    assert laser.peripheral_reads("workhead_ID") == [{"action": "get"}]
    assert state.workhead_id == "multi_function_carriage"
    assert state.basic_carriage == "fine_point_blade"


async def test_reads_the_blade_of_the_multi_function_carriage() -> None:
    laser = FakeM1Ultra()
    proto = _protocol(laser)
    state = XtoolDeviceState()
    await proto.poll_state(state)
    await proto.poll_state(state)
    assert laser.peripheral_reads("knife_head") == [{"action": "get"}]
    assert state.blade == "none"  # not synced yet
    laser.knife_head = dict(KNIFE_HEAD_ROTARY_BLADE)
    await proto.poll_state(state)
    assert state.blade == "rotary_blade"


async def test_a_module_swap_clears_the_blade() -> None:
    laser = FakeM1Ultra()
    laser.knife_head = dict(KNIFE_HEAD_ROTARY_BLADE)
    proto = _protocol(laser)
    state = XtoolDeviceState()
    await proto.poll_state(state)
    await proto.poll_state(state)
    assert state.blade == "rotary_blade"
    for push in SWAP_PUSHES:
        proto._dispatch_push(push)
    laser.workhead = {"drived": 42, "driving": 15, "drivingError": 0, "drivingLock": 0}
    await proto.poll_state(state)
    assert state.workhead_id == "laser_10w"
    assert state.basic_carriage == "fine_point_pen"
    assert state.blade is None
    laser.workhead = {"drived": 44, "driving": 31, "drivingError": 0, "drivingLock": 0}
    await proto.poll_state(state)
    assert state.workhead_id == "inkjet"
    assert state.basic_carriage == "hot_foil_pen"


async def test_unknown_ids_are_other() -> None:
    laser = FakeM1Ultra()
    laser.workhead = {"drived": 77, "driving": 99}
    state = XtoolDeviceState()
    await _protocol(laser).poll_state(state)
    assert state.workhead_id == tool_heads.OTHER
    assert state.basic_carriage == tool_heads.OTHER


async def test_no_tool_reads_while_asleep() -> None:
    laser = FakeM1Ultra()
    laser.mode = "P_SLEEP"
    state = XtoolDeviceState()
    state.workhead_id = "inkjet"
    await _protocol(laser).poll_state(state)
    assert not laser.peripheral_reads("workhead_ID")
    assert not laser.peripheral_reads("knife_head")
    # What was read before it fell asleep stays.
    assert state.workhead_id == "inkjet"


async def test_the_blade_is_read_again_once_homed() -> None:
    """Refused until the axes are homed, which a module swap undoes."""
    laser = FakeM1Ultra()
    laser.homed = False
    laser.knife_head = {"driving": 23}
    proto = _protocol(laser)
    state = XtoolDeviceState()
    await proto.poll_state(state)
    await proto.poll_state(state)
    assert state.blade is None
    assert "knife_head" not in proto._unsupported_peripheral_types
    laser.homed = True
    await proto.poll_state(state)
    assert state.blade == "cutting_blade"


def test_v1_replies_nest_the_ids_in_data() -> None:
    assert tool_heads.parse_workhead({"data": {"driving": 16, "drived": 44}}) == (
        "laser_20w", "hot_foil_pen",
    )
    assert tool_heads.parse_blade({"data": {"driving": 22}}) == "foil_transfer_tip"
    assert tool_heads.parse_workhead(None) == (None, None)
    assert tool_heads.parse_workhead({}) == (None, None)


def test_tool_sensors_are_diagnostic(hass: HomeAssistant, config_entry) -> None:
    for version in ("V1", "V2"):
        coordinator = build_coordinator(hass, _model(version))
        sensors = {
            s.translation_key: s for s in coordinator.build_sensors()
            if s.translation_key in TOOL_HEAD_SENSORS
        }
        assert set(sensors) == TOOL_HEAD_SENSORS, version
        for sensor in sensors.values():
            assert sensor.entity_category is EntityCategory.DIAGNOSTIC, version


def test_blade_unavailable_without_the_multi_function_carriage(
    hass: HomeAssistant, config_entry,
) -> None:
    for version in ("V1", "V2"):
        coordinator = build_coordinator(hass, _model(version))
        coordinator.power_switch_entity_id = None
        sensor = next(s for s in coordinator.build_sensors() if s.translation_key == "blade")
        state = XtoolDeviceState()
        state.available = True
        state.workhead_id = "laser_10w"
        coordinator.async_set_updated_data(state)
        assert not sensor.available, version
        state.workhead_id = "multi_function_carriage"
        coordinator.async_set_updated_data(state)
        assert sensor.available, version


def test_translations_cover_every_option() -> None:
    base = Path(__file__).resolve().parents[2] / "custom_components" / "xtool" / "translations"
    for lang in ("en", "de"):
        sensors = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))["entity"]["sensor"]
        for key, options in (
            ("workhead_id", tool_heads.TOOL_HEAD_OPTIONS),
            ("basic_carriage", tool_heads.BASIC_CARRIAGE_OPTIONS),
            ("blade", tool_heads.BLADE_OPTIONS),
        ):
            assert set(sensors[key]["state"]) == set(options), (lang, key)
