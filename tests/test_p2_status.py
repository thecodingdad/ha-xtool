"""The P2's status, progress, alarms and settings, read the way Studio reads them.

Replies are the ones a P2 (firmware 40.21.025) gave on 2026-11-07; the job
replies follow Studio's P2 extension (getCurrentStatus, the error table).
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import COMPONENT, has, requires

CONTROLS = has("p2_controls")

P2_SOURCE = (COMPONENT / "protocols" / "rest" / "p2.py").read_text(encoding="utf-8") if (COMPONENT / "protocols" / "rest" / "p2.py").exists() else ""

pytestmark = requires("p2_status")

WORKING = {
    "alarmInfo": {"alarm": None},
    "cpuTemp": 33,
    "curAlarmInfo": {"alarm": [
        {"module": "WATER_PUMP", "type": "CUR_LOW", "info": "", "level": "warn"},
    ]},
    "curMode": {"desc": "", "mode": "Work", "subMode": "working", "taskId": "t1"},
}
REPLIES: dict[str, Any] = {
    "/device/runningStatus": WORKING,
    "/processing/progress": {"time": 125, "total": 100, "value": 40},
    "/peripheral/gap": {"state": "off"},
    "/peripheral/drawer": {"state": "on"},
    "/peripheral/digital_lock": {"curClose": 0.0, "curOpen": 0.0, "limit_state": "off", "state": "off"},
    "/peripheral/fill_light": [{"idx": 1, "value": 0}, {"idx": 2, "value": 255}, {"idx": 3, "value": 255}],
    "/peripheral/ir_led": {"ir1": "on", "ir2": "off"},
    "/getsleeptimeout": {"result": "600"},
    "/getsleeptimeoutopengap": {"result": "180"},
    "/getFilllightAutoClosetimout": {"result": "180"},
    "/getIrlightAutoClosetimout": {"result": "120"},
    "/peripheral/cooling_fan?action=get": {"state": "on"},
    "/peripheral/smoking_fan?action=get": {"current": 0.0, "state": "off"},
    "/peripheral/water_pump?action=get": {"current": 0.0, "state": "on"},
    "/peripheral/water_tmp?action=get": {"value": 22.38},
    "/peripheral/water_flow?action=get": {"value": 107},
    "/peripheral/gyro?action=get": {"gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0, "pitch": 0.97},
}
CONFIG = {
    "beepEnable": False, "drawerCheck": False, "fillLightBrightness": 204, "flameAlarm": True,
    "flameAlarmLevel1": False, "flameLevelHLSelect": 1, "purifierEnable": False, "purifierTimeout": 5,
}


def p2():
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    return P2Protocol("127.0.0.1")


def fake(proto, monkeypatch, replies: dict[str, Any], config: dict | None = None) -> list[str]:
    """Answer reads from ``replies``; return the list of paths read."""
    calls: list[str] = []

    async def get_json(path, timeout=5.0):
        calls.append(path)
        return replies.get(path)

    async def post_json(path, data=None, timeout=5.0):
        calls.append(path)
        if path == "/config/get":
            return config
        if path == "/peripheral/laser_head":
            return {"x": 10.0, "y": 20.0}
        return None

    monkeypatch.setattr(proto, "_get_json", get_json)
    monkeypatch.setattr(proto, "_post_json", post_json)
    return calls


def test_alarms_read_as_studio_shows_them() -> None:
    """Alarms carry Studio's code and English title."""
    from custom_components.xtool.protocols.rest.p2 import describe_alarm

    assert describe_alarm({"module": "WATER_PUMP", "type": "CUR_LOW", "info": "", "level": "warn"}) == {
        "code": "Qx08_0", "message": "Water pump current too low", "level": "warn",
        "event_type": "water_cooling",
    }
    tilt = describe_alarm({"module": "GYRO_SENSOR", "type": "MACHINE_TILTED", "info": ""})
    assert tilt["code"] == "Qx_x_1" and tilt["event_type"] == "tilt"
    board = describe_alarm({"module": "BOARDS", "type": "ELEMENT_NOT_FOUND", "info": "x:ui_gd330"})
    assert board["code"] == "Qx30_1_2"
    assert describe_alarm({"module": "BOARDS", "type": "CONNNECT", "info": "ext_firebox_esp32"}) is None


async def test_poll_reads_a_running_job(monkeypatch) -> None:
    """A job reads Processing with its progress, time, alarms and peripherals."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    proto = p2()
    fake(proto, monkeypatch, REPLIES, CONFIG)
    state = XtoolDeviceState()
    await proto.poll_state(state)
    assert state.status == "processing"
    assert state.task_id == "t1"
    assert state.task_progress == 40.0
    assert state.task_time == 125
    assert state.cpu_temp == 33
    assert [a["code"] for a in state.active_alarms] == ["Qx08_0"]
    assert state.cover_open is False
    assert state.cover_locked is False
    assert state.fill_light_a == 100
    assert state.ir_led_close is True and state.ir_led_global is False
    if "/getsleeptimeout" in P2_SOURCE:
        # Until the P2 rework dropped them (the P2 refuses to set them)
        assert state.sleep_timeout == 600
        assert state.ir_light_auto_off == 120
    assert state.beep_enabled is False
    assert state.flame_alarm == 1  # on, Low
    assert state.water_temperature == 22.38
    assert state.cooling_fan_running is True
    assert state.position_x == 10.0


async def test_settings_are_read_once_a_minute(monkeypatch) -> None:
    """Every poll reads the status; the rest waits a minute (the settings too, before they were read on every poll)."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    proto = p2()
    calls = fake(proto, monkeypatch, REPLIES, CONFIG)
    await proto.poll_state(XtoolDeviceState())
    first = len(calls)
    assert calls.count("/config/get") == 1
    calls.clear()
    await proto.poll_state(XtoolDeviceState())
    assert ("/config/get" in calls) is CONTROLS
    assert "/device/workingInfo" not in calls
    assert "/device/runningStatus" in calls
    assert len(calls) < first


def test_a_stopped_job_reads_cancelled() -> None:
    """Studio: WORK_STOPED and then the job ending is a cancel, not a finish."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    proto = p2()
    proto.handle_push({"module": "STATUS_CONTROLLER", "type": "WORK_STOPED", "info": "", "level": "notice"})
    proto.handle_push({"module": "STATUS_CONTROLLER", "type": "MODE_CHANGE", "level": "notice",
                       "info": {"mode": "P_WORK_DONE", "subMode": "", "taskId": "t1"}})
    state = XtoolDeviceState()
    proto.apply_latest(state)
    assert state.status == "cancelling"
    proto.handle_push({"module": "STATUS_CONTROLLER", "type": "MODE_CHANGE", "level": "notice",
                       "info": {"mode": "P_IDLE", "subMode": "", "taskId": "t1"}})
    proto.apply_latest(state)
    assert state.status == "idle"
    # The next job finishes normally
    proto.handle_push({"module": "STATUS_CONTROLLER", "type": "MODE_CHANGE", "level": "notice",
                       "info": {"mode": "P_WORK_DONE", "subMode": "", "taskId": "t2"}})
    proto.apply_latest(state)
    assert state.status == "finished"


def test_an_error_push_is_an_error_event() -> None:
    """An error push becomes an Error event with Studio's code and message."""
    proto = p2()
    proto.handle_push({"module": "SMOKING_FAN", "type": "CUR_LOW", "info": "", "level": "warn"})
    assert proto.take_push_events() == [
        ("error", "exhaust_fan", {"code": "Qx32_0", "message": "Fan current too low", "level": "warn"}),
    ]


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.protocols.rest import XTOOL_P2

    coord = build_coordinator(hass, XTOOL_P2)
    coord.sent = []
    coord._emit_event = lambda kind, event_type, attributes=None: coord.sent.append((kind, event_type, attributes))
    return coord


def test_new_alarms_are_events_once(coordinator) -> None:
    """An alarm that appears is one event; one already pushed is not sent again."""
    pump = {"code": "Qx08_0", "message": "Water pump current too low", "level": "warn", "event_type": "water_cooling"}
    fire = {"code": "Qx_x_3", "message": "Flame detected", "level": "fatal", "event_type": "fire"}
    coordinator._emit_alarm_changes([], [pump])
    assert coordinator.sent == [("error", "water_cooling", {"code": "Qx08_0", "message": "Water pump current too low", "level": "warn"})]
    coordinator.sent.clear()
    coordinator._emit_alarm_changes([pump], [pump, fire])
    assert coordinator.sent == [("fire_warning", "triggered", {"level": "fatal"})]
    coordinator.sent.clear()
    coordinator._emit_alarm_changes([pump, fire], [])
    assert coordinator.sent == [("fire_warning", "cleared", None)]
    coordinator.sent.clear()
    coordinator._announced.add("Qx08_0")
    coordinator._emit_alarm_changes([], [pump])
    assert coordinator.sent == []


def test_p2_entities(coordinator) -> None:
    """The P2 has Task progress, CPU temperature, Active alarms and its own Error event."""
    from custom_components.xtool.protocols.rest.p2 import P2_ERROR_EVENT_TYPES

    keys = {e.translation_key for e in coordinator.build_sensors()}
    assert {"task_progress", "cpu_temp"} <= keys
    alarm_sensors = {e.translation_key for e in coordinator.build_binary_sensors()}
    # The Alarm problem sensor replaced the Active alarms count
    assert ("active_alarms" in keys) is not CONTROLS
    assert ("alarm" in alarm_sensors) is CONTROLS
    error = next(e for e in coordinator.build_events() if e.translation_key == "error")
    assert error.event_types == list(P2_ERROR_EVENT_TYPES)
    assert "water_cooling" in error.event_types and "fire" not in error.event_types
