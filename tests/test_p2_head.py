"""P2: Laser position X and Y, Move laser, the Move action, the measurements."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import has, COMPONENT, requires

# Where the P2's head stops (Studio's limits were used before)
MAX_X, MAX_Y = (610, 305) if has("p2_real_travel") else (620, 308)

pytestmark = requires("p2_head_controls")


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.const import XtoolStatus
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest import XTOOL_P2

    coord = build_coordinator(hass, XTOOL_P2)
    coord.data = XtoolDeviceState(status=XtoolStatus.IDLE)
    coord.protocol.move_laser_head = AsyncMock()
    coord.protocol.read_head_position = AsyncMock(return_value=(100.0, 50.0))
    return coord


def _numbers(coordinator):
    entities = coordinator.build_numbers()
    for entity in entities:
        entity.async_write_ha_state = MagicMock()
    return {e.translation_key: e for e in entities}


def test_positions_are_numbers_not_sensors(coordinator) -> None:
    numbers = _numbers(coordinator)
    assert numbers["laser_position_x"].native_max_value == MAX_X
    assert numbers["laser_position_y"].native_max_value == MAX_Y
    assert numbers["laser_position_x"].mode == "box"
    sensors = {e.translation_key for e in coordinator.build_sensors()}
    assert not {"laser_position_x", "laser_position_y"} & sensors
    assert "measured_thickness_or_distance" in sensors
    exposure = numbers["exposure"]
    assert (exposure.mode, exposure.native_step, exposure.native_max_value) == ("slider", 10, 1000)


def _move(coordinator, **move):
    from custom_components.xtool.protocols.rest.p2_entities import async_move_laser_head

    return async_move_laser_head(coordinator, **move)


def _button(coordinator):
    return next(e for e in coordinator.build_buttons() if e.translation_key == "move_laser")


async def test_x_and_y_wait_for_move_laser(coordinator) -> None:
    numbers = _numbers(coordinator)
    coordinator.async_update_listeners = MagicMock()
    await numbers["laser_position_x"].async_set_native_value(200.0)
    coordinator.protocol.move_laser_head.assert_not_awaited()
    assert numbers["laser_position_x"].native_value == 200.0  # shows the value set
    attributes = numbers["laser_position_x"].extra_state_attributes
    assert attributes["waiting"] is True
    assert attributes["position"] == coordinator.data.position_x  # where the head is
    await numbers["laser_position_y"].async_set_native_value(80.0)
    await _button(coordinator).async_press()
    coordinator.protocol.move_laser_head.assert_awaited_once_with(200.0, 80.0)
    assert numbers["laser_position_x"].extra_state_attributes["waiting"] is False
    assert (numbers["laser_position_x"].native_value, numbers["laser_position_y"].native_value) == (
        200.0, 80.0,
    )


async def test_move_laser_keeps_an_axis_not_set(coordinator) -> None:
    coordinator.async_update_listeners = MagicMock()
    await _numbers(coordinator)["laser_position_x"].async_set_native_value(200.0)
    await _button(coordinator).async_press()
    coordinator.protocol.move_laser_head.assert_awaited_once_with(200.0, 50.0)
    with pytest.raises(HomeAssistantError, match="set Laser position X or Y first"):
        await _button(coordinator).async_press()


async def test_steps_start_from_the_position_read_now(coordinator) -> None:
    result = await _move(coordinator, step_x=10.0, step_y=-5.0, steps=3)
    coordinator.protocol.read_head_position.assert_awaited_once()
    coordinator.protocol.move_laser_head.assert_awaited_once_with(130.0, 35.0)
    assert result == {"x": 130.0, "y": 35.0}


async def test_both_positions_need_no_read(coordinator) -> None:
    await _move(coordinator, x=10.0, y=20.0)
    coordinator.protocol.read_head_position.assert_not_awaited()
    coordinator.protocol.move_laser_head.assert_awaited_once_with(10.0, 20.0)


async def test_a_move_past_the_limit_stops_there_and_says_so(coordinator) -> None:
    with pytest.raises(HomeAssistantError) as err:
        await _move(coordinator, step_x=-40.0, steps=3, y=400.0)
    coordinator.protocol.move_laser_head.assert_awaited_once_with(0.0, float(MAX_Y))
    assert str(err.value) == (
        "Laser head: X moved to 0.00 mm, the lower limit. 3 steps of -40.00 mm "
        f"from 100.00 mm would have gone to -20.00 mm. Y moved to {MAX_Y}.00 mm, the "
        f"upper limit. 400.00 mm is outside 0-{MAX_Y} mm."
    )
    assert (coordinator.data.position_x, coordinator.data.position_y) == (0.0, float(MAX_Y))


async def test_moves_run_one_at_a_time_from_where_the_last_ended(coordinator) -> None:
    import asyncio

    position = [100.0, 50.0]
    started: list[tuple[float, float]] = []
    release = asyncio.Event()

    async def read():
        return tuple(position)

    async def move(x, y):
        started.append((x, y))
        if len(started) == 1:
            await release.wait()
        position[:] = [x, y]

    coordinator.protocol.read_head_position = AsyncMock(side_effect=read)
    coordinator.protocol.move_laser_head = AsyncMock(side_effect=move)
    first = asyncio.create_task(_move(coordinator, step_x=10.0))
    second = asyncio.create_task(_move(coordinator, step_x=10.0))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert started == [(110.0, 50.0)]
    release.set()
    assert await first == {"x": 110.0, "y": 50.0}
    assert await second == {"x": 120.0, "y": 50.0}


async def test_no_move_unless_idle(coordinator) -> None:
    from custom_components.xtool.const import XtoolStatus

    coordinator.data.status = XtoolStatus.PROCESSING
    coordinator.async_update_listeners = MagicMock()
    await _numbers(coordinator)["laser_position_x"].async_set_native_value(5.0)
    with pytest.raises(HomeAssistantError, match="not moved"):
        await _button(coordinator).async_press()
    coordinator.protocol.move_laser_head.assert_not_awaited()
    # The value set still waits
    assert _numbers(coordinator)["laser_position_x"].native_value == 5.0


async def test_the_head_moves_while_the_laser_sleeps(coordinator) -> None:
    from custom_components.xtool.const import XtoolStatus

    coordinator.data.status = XtoolStatus.SLEEPING
    await _move(coordinator, x=300.0, y=225.0)
    coordinator.protocol.move_laser_head.assert_awaited_once_with(300.0, 225.0)


async def test_the_action_takes_a_p2_device(coordinator, monkeypatch) -> None:
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import device_registry as dr

    from custom_components.xtool.const import DOMAIN
    from custom_components.xtool.services import _async_move

    hass = coordinator.hass
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = coordinator
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "MXTEST0001")}, model="xTool P2",
    )
    monkeypatch.setattr(
        "custom_components.xtool.protocols.rest.p2_entities.p2_can_move", lambda c: c is coordinator,
    )

    def call(**data):
        return MagicMock(hass=hass, data={"device_id": device.id, "steps": 1, **data})

    with pytest.raises(ServiceValidationError):
        await _async_move(call())  # no position or step
    assert await _async_move(call(step_y=2.5, steps=2)) == {"x": 100.0, "y": 55.0}
    with pytest.raises(ServiceValidationError):
        await _async_move(MagicMock(hass=hass, data={"device_id": "nope", "x": 1.0, "steps": 1}))
    monkeypatch.setattr(
        "custom_components.xtool.protocols.rest.p2_entities.p2_can_move", lambda c: False,
    )
    with pytest.raises(ServiceValidationError):
        await _async_move(call(x=1.0))


def test_the_action_is_described() -> None:
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
    move = services["move"]
    assert "target" not in move
    assert set(move["fields"]) == {"device_id", "x", "y", "step_x", "step_y", "steps"}
    assert move["fields"]["device_id"]["selector"]["device"]["filter"] == [
        {"integration": "xtool", "model": "xTool P2"},
    ]
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        assert set(data["services"]["move"]["fields"]) == {
            "device_id", "x", "y", "step_x", "step_y", "steps",
        }
        assert data["entity"]["number"]["laser_position_x"]["name"] == "Laser position X"
        assert data["entity"]["button"]["move_laser"]["name"] == "Move laser"
        assert data["entity"]["sensor"]["measured_thickness_or_distance"]["name"] == (
            "Measured thickness or distance"
        )
        assert data["services"]["move"]["name"] == "Move"
        assert data["entity"]["button"]["home_laser_head"]["name"] == "Home laser"


def test_measure_offset_reads_the_p2_setting() -> None:
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    state = XtoolDeviceState()
    proto = P2Protocol("127.0.0.1")
    assert proto.measure_offset(state) == {}
    state.device_config = {"laserMeasureOffset": "X64.3Y0.9Z136.7"}
    assert proto.measure_offset(state) == {"x": 64.3, "y": 0.9, "z": 136.7}


def _measure_setup(coordinator, monkeypatch, readings):
    from custom_components.xtool.protocols.rest import p2_entities

    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(p2_entities.asyncio, "sleep", sleep)
    coordinator.protocol.measure_material = AsyncMock(side_effect=readings)
    coordinator.protocol.measure_offset = MagicMock(return_value={"x": 64.3, "y": 0.9, "z": 136.7})
    coordinator.protocol.set_ir_led = AsyncMock()
    coordinator.data.ir_led_close = False
    buttons = {e.translation_key: e for e in coordinator.build_buttons()}
    return buttons["measure_thickness_or_distance"], waits


@requires("p2_sensor_distance")
async def test_measure_gives_the_thickness_and_where(coordinator, monkeypatch) -> None:
    from custom_components.xtool.protocols.rest import p2_entities
    from custom_components.xtool.protocols.rest.p2_entities import P2Measured

    # The sensor's own distance; the thickness is Z minus it
    button, waits = _measure_setup(coordinator, monkeypatch, [
        {"distance": 130.7, "coord_x": 10.0, "coord_y": 20.0},
    ])
    await button.async_press()
    assert [c.args for c in coordinator.protocol.set_ir_led.await_args_list] == [(1, True), (1, False)]
    # The sensor starts with the red dot: it is not asked before
    assert waits == [p2_entities.P2_SENSOR_START_SECONDS]
    assert coordinator.data.last_distance_mm == 130.7
    # The result goes into Thickness or distance, as the Mode takes it
    assert coordinator._p2_view["thickness_or_distance"] == 6.0
    assert coordinator.data.measured_thickness_mm == 6.0
    measured = P2Measured(coordinator)
    measured._seen_live_data = True
    assert measured.native_value == 6.0  # Process on slats: the thickness
    assert measured.extra_state_attributes == {
        "thickness": 6.0, "sensor_distance": 130.7, "x": 74.3, "y": 20.9,
    }
    coordinator._p2_view["processing_mode"] = "slats_removed"
    assert measured.native_value == 19.0  # Studio's distance


@requires("p2_sensor_distance")
async def test_measure_asks_again_until_the_sensor_has_read(coordinator, monkeypatch) -> None:
    from custom_components.xtool.protocols.rest import p2_entities

    # 0: no reading yet; -1: the P2 could not talk to the sensor
    button, waits = _measure_setup(coordinator, monkeypatch, [
        {"distance": 0.0}, {"distance": -1.0}, {"distance": 135.2, "coord_x": 0.0, "coord_y": 0.0},
    ])
    await button.async_press()
    assert coordinator.data.measured_thickness_mm == 1.5
    assert coordinator.protocol.measure_material.await_count == 3
    # A sensor that never reads says so, and the dot is turned off again
    coordinator.protocol.measure_material = AsyncMock(return_value={"distance": 0.0})
    coordinator.protocol.set_ir_led.reset_mock()
    with pytest.raises(HomeAssistantError, match="did not read"):
        await button.async_press()
    assert coordinator.protocol.measure_material.await_count == p2_entities.P2_MEASURE_TRIES
    assert [c.args for c in coordinator.protocol.set_ir_led.await_args_list] == [(1, True), (1, False)]
    assert coordinator.data.measured_thickness_mm == 1.5


@requires("p2_sensor_distance")
async def test_a_measure_the_mode_cannot_take_is_refused(coordinator, monkeypatch) -> None:
    # Process on slats takes 0 to 25 mm: 177.4 mm read is 40.7 mm below the slats
    button, _ = _measure_setup(coordinator, monkeypatch, [
        {"distance": 177.4, "coord_x": 0.0, "coord_y": 0.0},
        {"distance": 177.4, "coord_x": 0.0, "coord_y": 0.0},
    ])
    from custom_components.xtool.protocols.rest.p2_entities import _p2_view

    before = _p2_view(coordinator)["thickness_or_distance"]
    with pytest.raises(HomeAssistantError, match="outside this Mode's 0 to 25 mm"):
        await button.async_press()
    assert _p2_view(coordinator)["thickness_or_distance"] == before
    assert coordinator.data.measured_thickness_mm is None
    # Slats removed takes it: 65.7 mm down from 25 mm above the slats
    coordinator._p2_view["processing_mode"] = "slats_removed"
    await button.async_press()
    assert coordinator._p2_view["thickness_or_distance"] == 65.7


@requires("p2_sensor_distance")
async def test_a_measure_without_a_reply_says_so(coordinator, monkeypatch) -> None:
    button, waits = _measure_setup(coordinator, monkeypatch, [None])
    coordinator.data.ir_led_close = True  # left on: not turned off, no wait
    with pytest.raises(HomeAssistantError):
        await button.async_press()
    coordinator.protocol.set_ir_led.assert_not_awaited()
    assert waits == []
    # Without the P2's sensor setting there is no thickness
    coordinator.protocol.measure_offset = MagicMock(return_value={})
    with pytest.raises(HomeAssistantError, match="laserMeasureOffset"):
        await button.async_press()


@requires("p2_sensor_distance")
async def test_the_protocol_reads_the_position_and_the_measurement(monkeypatch) -> None:
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("127.0.0.1")
    replies = {"/peripheral/laser_head": {"x": 1.5, "y": 2, "z": 0}}

    async def post_json(path, data=None, timeout=5.0):
        return replies.get(path)

    async def get_json(path, timeout=5.0):
        assert path == "/peripheral/quest/ir_measure_distance"
        return {"distance": 134.9, "coord_x": 0, "coord_y": 0}

    monkeypatch.setattr(proto, "_post_json", post_json)
    monkeypatch.setattr(proto, "_get_json", get_json)
    assert await proto.read_head_position() == (1.5, 2.0)
    assert await proto.measure_material() == {"distance": 134.9, "coord_x": 0.0, "coord_y": 0.0}
    replies["/peripheral/laser_head"] = {"x": None}
    assert await proto.read_head_position() is None


@requires("p2_sensor_distance")
def test_the_p2s_warnings_go_to_the_log(caplog) -> None:
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("127.0.0.1")
    alarm = {"info": "", "level": "warnning", "module": "MEASURE_DISTANCE_SENSOR", "type": "COMMUNI_ABNORMAL"}
    running = {"curAlarmInfo": {"alarm": [alarm]}}
    apply = next(
        getattr(proto, name) for name in ("_apply_running", "_apply_running_status", "_apply_status")
        if hasattr(proto, name)
    )
    with caplog.at_level("WARNING"):
        apply(XtoolDeviceState(), running)
        apply(XtoolDeviceState(), running)
    lines = [r.message for r in caplog.records if "The P2 reports" in r.message]
    assert lines == ["The P2 reports: warnning MEASURE_DISTANCE_SENSOR COMMUNI_ABNORMAL"]
    # Gone and back: logged again
    apply(XtoolDeviceState(), {"curAlarmInfo": {"alarm": None}})
    with caplog.at_level("WARNING"):
        apply(XtoolDeviceState(), running)
    assert len([r for r in caplog.records if "The P2 reports" in r.message]) == 2
