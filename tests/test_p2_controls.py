"""P2: Safety lock as a lock, Alarm sensor, Baseplate states, laser head moves."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import COMPONENT, has, requires

pytestmark = requires("p2_controls")


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest import XTOOL_P2

    coord = build_coordinator(hass, XTOOL_P2)
    coord.data = XtoolDeviceState()
    return coord


def _by_key(entities):
    for entity in entities:
        entity.async_write_ha_state = MagicMock()
    return {e.translation_key: e for e in entities}


async def test_safety_lock_locks_and_unlocks(coordinator) -> None:
    coordinator.protocol.set_digital_lock = AsyncMock()
    lock = _by_key(coordinator.build_locks())["safety_lock"]
    assert lock.unique_id.endswith("digital_lock")
    await lock.async_lock()
    coordinator.protocol.set_digital_lock.assert_awaited_with(True)
    assert lock.is_locked is True
    await lock.async_unlock()
    coordinator.protocol.set_digital_lock.assert_awaited_with(False)
    assert lock.is_locked is False


def test_alarm_is_on_while_an_alarm_is_active(coordinator) -> None:
    alarm = _by_key(coordinator.build_binary_sensors())["alarm"]
    assert alarm.device_class == "problem"
    assert alarm.is_on is False
    coordinator.data.active_alarms = [{"code": "Qx08_0", "message": "Water pump", "level": "error"}]
    assert alarm.is_on is True
    assert alarm.extra_state_attributes["codes"] == ["Qx08_0"]


def test_baseplate_reads_installed_or_removed() -> None:
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        states = data["entity"]["binary_sensor"]["baseplate"]["state"]
        assert states == {"on": "Installed", "off": "Removed"}
        assert data["entity"]["lock"]["safety_lock"]["name"] == "Safety lock"


async def test_a_move_waits_and_the_next_poll_reads_the_position(monkeypatch) -> None:
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("127.0.0.1")
    sent: list[tuple[str, dict, float]] = []

    async def post(path, data=None, timeout=5.0):
        sent.append((path, data, timeout))
        return "ok"

    position = {"x": 0.0, "y": 0.0}
    coords: list[str] = []

    async def post_json(path, data=None, timeout=5.0):
        if path == "/peripheral/laser_head":
            coords.append(path)
            return dict(position)
        return None

    async def get_json(path, timeout=5.0):
        return {"curMode": {"mode": "P_IDLE"}} if path == "/device/runningStatus" else None

    monkeypatch.setattr(proto, "_post", post)
    monkeypatch.setattr(proto, "_post_json", post_json)
    monkeypatch.setattr(proto, "_get_json", get_json)

    state = XtoolDeviceState()
    await proto.poll_state(state)
    coords.clear()
    await proto.poll_state(state)
    from .features import has

    if has("p2_position_every_poll"):
        assert coords == ["/peripheral/laser_head"]  # it moves while the P2 homes
    else:
        assert coords == []  # the position waits a minute

    await proto.move_laser_head(100.0, 50.0)
    path, data, timeout = sent[-1]
    assert path == "/peripheral/laser_head"
    assert data == {"action": "go_to", "x": 100.0, "y": 50.0, "f": 9600, "waitTime": 30000}
    assert timeout > 30

    position.update(x=100.0, y=50.0)
    await proto.poll_state(state)
    assert coords and (state.position_x, state.position_y) == (100.0, 50.0)
    coords.clear()
    await proto.poll_state(state)
    assert coords == (["/peripheral/laser_head"] if has("p2_position_every_poll") else [])


def test_position_icons_are_straight_arrows(coordinator) -> None:
    if has("p2_head_controls"):
        icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))["entity"]["number"]
        assert icons["laser_position_x"]["default"] == "mdi:arrow-left-right"
        assert icons["laser_position_y"]["default"] == "mdi:arrow-up-down"
        return
    sensors = _by_key(coordinator.build_sensors())
    assert sensors["laser_position_x"].icon == "mdi:arrow-left-right"
    assert sensors["laser_position_y"].icon == "mdi:arrow-up-down"
