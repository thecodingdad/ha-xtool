"""The xTool X1 (JS001), as a real X1 behaves.

Built from what an X1 on firmware 40.140.003.01.ht18 answered (see
x1_capture.py) and, for the pushes it did not send while captured
(feeder sensors, job notices), from the shapes Studio's X1 extension
(``xcs-ext-js001``) reads. The X1 shares the M2's platform 2.0 code:
when one of these tests fails after a change made for the M2, that
change reached the X1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.const import DOMAIN, XtoolStatus
from custom_components.xtool.protocols import DEVICE_MODELS, detect_models
from custom_components.xtool.protocols.base import XtoolDeviceState

from .x1_capture import (
    FILL_LIGHT_REPORT,
    FIRMWARE,
    HOST,
    KEY_NOTICE,
    LID_CLOSED_REPORT,
    LID_OPENED_REPORT,
    SECURE_CONFIRM_REPORT,
    SERIAL,
    STATE_REPORT,
    TASK_ID,
    FakeX1,
)

X1 = DEVICE_MODELS["X1_V2"]

# What the X1 creates, as (platform, key).
EXPECTED_ENTITIES = {
    ("binary_sensor", "alarm"),
    ("binary_sensor", "cover_open"),
    ("binary_sensor", "diode_laser_installed"),
    ("binary_sensor", "feeder_front_sensor"),
    ("binary_sensor", "feeder_installed"),
    ("binary_sensor", "feeder_rear_sensor"),
    ("binary_sensor", "inkjet_module_installed"),
    ("binary_sensor", "machine_lock"),
    ("button", "cancel_job"),
    ("button", "pause_job"),
    ("button", "resume_job"),
    ("camera", "camera_far"),
    ("camera", "camera_near"),
    ("event", "error"),
    ("event", "job"),
    ("light", "fill_light_left"),
    ("light", "fill_light_near"),
    ("light", "fill_light_right"),
    ("number", "camera_far_thumbnail_refresh"),
    ("number", "camera_near_thumbnail_refresh"),
    ("number", "smoking_fan_duration"),
    ("select", "exhaust_fan_speed"),
    ("select", "flame_alarm_sensitivity"),
    ("sensor", "cpu_temp"),
    ("sensor", "last_job_time"),
    ("sensor", "laser_backpack"),
    ("sensor", "position_x"),
    ("sensor", "position_y"),
    ("sensor", "session_count"),
    ("sensor", "standby_seconds"),
    ("sensor", "status"),
    ("sensor", "task_id"),
    ("sensor", "task_time"),
    ("sensor", "wifi_signal"),
    ("sensor", "working_seconds"),
    ("switch", "beep_enable"),
    ("update", "firmware_update"),
}

# The states the captured replies show.
EXPECTED_STATES = {
    "binary_sensor.alarm": "off",
    "binary_sensor.cover_open": "off",
    "binary_sensor.diode_laser_installed": "on",
    "binary_sensor.feeder_front_sensor": "off",
    "binary_sensor.feeder_installed": "on",
    "binary_sensor.feeder_rear_sensor": "off",
    "binary_sensor.inkjet_module_installed": "off",
    "binary_sensor.machine_lock": "on",
    "light.fill_light_left": "on",
    "light.fill_light_near": "off",
    "light.fill_light_right": "on",
    "number.smoking_fan_duration": "20.0",
    "select.exhaust_fan_speed": "75",
    "select.flame_alarm_sensitivity": "high",
    "sensor.laser_backpack": "none",
    "sensor.position_x": "0.0",
    "sensor.position_y": "0.0",
    "sensor.session_count": "13",
    "sensor.standby_seconds": "18022",
    "sensor.status": "idle",
    "sensor.wifi_signal": "-55",
    "sensor.working_seconds": "1.315",
    "switch.beep_enable": "on",
}


def entity_id(short: str) -> str:
    """``switch.beep_enable`` → ``switch.xtool_x1_<serial>_beep_enable``."""
    domain, key = short.split(".", 1)
    return f"{domain}.xtool_x1_{SERIAL.lower()}_{key}"


@pytest.fixture
async def x1(hass: HomeAssistant) -> AsyncIterator[tuple[MockConfigEntry, FakeX1]]:
    """The integration set up against the captured X1."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SERIAL, title="xTool X1",
        data={
            "host": HOST, "serial_number": SERIAL, "device_name": "xTool_X1_000000",
            "firmware_version": FIRMWARE, "laser_power_watts": 0,
            "model_key": "X1_V2", "pairing_token": "test-token",
        },
    )
    entry.add_to_hass(hass)
    with patch.object(X1, "protocol_class", FakeX1):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry, entry.runtime_data.protocol
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


def _protocol() -> FakeX1:
    protocol = FakeX1(HOST)
    protocol.set_model(X1)
    protocol._connected = True
    return protocol


def _report(url: str, payload: dict) -> dict:
    return {"type": "request", "method": "REPORT", "params": {}, "data": {url: payload}}


def _notice(name: str, **entry) -> dict:
    return {"type": "request", "method": "NOTIFY", "params": {}, "data": [{"name": name, "level": "NOTICE", **entry}]}


# --- Identity -----------------------------------------------------------


def test_found_by_its_discovery_name() -> None:
    for name in ("xTool_X1_000000", "JS001"):
        assert [m for m in detect_models(name) if m.protocol_version == "V2"] == [X1]


async def test_device_info() -> None:
    info = await _protocol().get_device_info()
    assert (info.serial_number, info.device_name, info.main_firmware) == (SERIAL, "xTool_X1_000000", FIRMWARE)


# --- Entities and their states -----------------------------------------


async def test_the_x1_creates_its_entities(hass: HomeAssistant, x1) -> None:
    entry, _ = x1
    registry = er.async_get(hass)
    created = {
        (e.domain, e.unique_id.removeprefix(f"{SERIAL}_"))
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert created == EXPECTED_ENTITIES


async def test_the_entities_show_what_the_x1_reports(hass: HomeAssistant, x1) -> None:
    shown = {short: hass.states.get(entity_id(short)) for short in EXPECTED_STATES}
    assert {short: s.state if s else None for short, s in shown.items()} == EXPECTED_STATES
    assert shown["light.fill_light_left"].attributes["brightness"] == 255
    assert shown["select.exhaust_fan_speed"].attributes["options"] == ["75", "90", "100"]
    assert shown["select.flame_alarm_sensitivity"].attributes["options"] == ["low", "high"]


async def test_the_device_page(hass: HomeAssistant, x1) -> None:
    from homeassistant.helpers import device_registry as dr

    entry, _ = x1
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    device = next(d for d in devices if (DOMAIN, SERIAL) in d.identifiers)
    assert (device.manufacturer, device.sw_version) == ("xTool", FIRMWARE)


async def test_the_poll_reads_every_value() -> None:
    protocol = _protocol()
    state = XtoolDeviceState()
    await protocol.poll_state(state)
    assert (state.status, state.task_id) == (XtoolStatus.IDLE, TASK_ID)
    assert (state.cover_open, state.machine_lock) == (False, True)
    assert (state.position_x, state.position_y) == (0.0, 0.0)
    # ``top`` is not one of Studio's lights
    assert (state.fill_light_left, state.fill_light_right, state.fill_light_near) == (100, 100, 0)
    assert (state.feeder_installed, state.feeder_front_blocked, state.feeder_rear_blocked) == (True, False, False)
    assert (state.diode_laser_installed, state.laser_backpack, state.inkjet_module_installed) == (True, "none", False)
    assert (state.wifi_signal, state.alarm_present) == (-55, False)
    assert (state.smoking_fan_duration, state.exhaust_fan_speed, state.flame_sensitivity) == (20, 750, 3)
    assert (state.working_seconds, state.standby_seconds, state.session_count) == (4734, 18022, 13)
    # The settings are read with Studio's key list
    assert ("GET", "/v1/platform/device/config", None, {
        "kv": ["smokeFanTimeout", "smokeFanSpeed", "beepEnable", "flameSensitivity"],
    }) in protocol.sent
    assert protocol._latest["beep_enabled_v2"] is True


async def test_the_slow_reads_run_every_sixth_poll() -> None:
    protocol = _protocol()
    for _ in range(6):
        await protocol.poll_state(XtoolDeviceState())
    urls = [url for _, url, _, _ in protocol.sent]
    assert urls.count("/v1/project/peripheral/lid-status") == 6
    assert urls.count("/v1/project/peripheral/fill-light") == 1


@pytest.mark.parametrize(("backpack", "shown"), [
    ("RED_30W", "fiber_30w"), ("RED_60W", "mopa_60w"), ("UV", "uv_5w"), ("UV_RED_60W", "uv_mopa"),
])
async def test_laser_backpacks(backpack: str, shown: str) -> None:
    protocol = _protocol()
    protocol.replies[("GET", "/v1/project/peripheral/laserbag-info")] = {"exist": True, "type": backpack}
    state = XtoolDeviceState()
    await protocol.poll_state(state)
    assert state.laser_backpack == shown


async def test_active_alarms_show_on_the_alarm_sensor() -> None:
    protocol = _protocol()
    protocol.replies[("GET", "/v1/project/alarm/records")] = {
        "alarms-list": [{"code": "0x0401", "level": "ERROR"}], "alarms-record": [],
    }
    state = XtoolDeviceState()
    await protocol.poll_state(state)
    assert state.alarm_present is True


# --- Status and pushes ----------------------------------------------------


@pytest.mark.parametrize(("mode", "status"), [
    ("Initial", XtoolStatus.INITIALIZING),
    ("Idle", XtoolStatus.IDLE),
    ("Busy", XtoolStatus.WORKING_API),
    ("Measure", XtoolStatus.MEASURING),
    ("Autowork", XtoolStatus.PROCESSING),
    ("WorkReady", XtoolStatus.PROCESSING_READY),
    ("WorkPlay", XtoolStatus.PROCESSING),
    ("WorkPause", XtoolStatus.PAUSED),
    ("Sleep", XtoolStatus.SLEEPING),
    ("Upgrade", XtoolStatus.FIRMWARE_UPDATE),
    ("Network", XtoolStatus.WIFI_SETUP),
    ("Error", XtoolStatus.ERROR_LIMIT),
])
def test_status_from_the_state_push(mode: str, status: XtoolStatus) -> None:
    protocol = _protocol()
    protocol._dispatch_push(_report("/v1/platform/device/state", {"mode": mode, "taskId": "T-1"}))
    assert protocol._latest["status"] is status
    assert protocol._latest["task_id"] == "T-1"


def test_waiting_for_the_pairing_button_is_idle() -> None:
    """The X1 reports SecureConfirm while it waits for its button; Studio counts it as not busy."""
    protocol = _protocol()
    protocol._dispatch_push(SECURE_CONFIRM_REPORT)
    assert protocol._latest["status"] is XtoolStatus.IDLE
    protocol._dispatch_push(KEY_NOTICE)
    protocol._dispatch_push(STATE_REPORT)
    assert protocol._latest["status"] is XtoolStatus.IDLE


def test_lid_and_safety_key_pushes() -> None:
    protocol = _protocol()
    protocol._dispatch_push(LID_OPENED_REPORT)
    protocol._dispatch_push(_report("/v1/project/peripheral/usbkey", {"state": False}))
    assert (protocol._latest["cover_open"], protocol._latest["machine_lock"]) == (True, False)
    protocol._dispatch_push(LID_CLOSED_REPORT)
    assert protocol._latest["cover_open"] is False


async def test_the_cover_follows_the_lid_at_once(hass: HomeAssistant, x1) -> None:
    """Opened and closed on the X1: the report shows without a poll."""
    _, protocol = x1
    protocol._dispatch_event(LID_OPENED_REPORT)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id("binary_sensor.cover_open")).state == "on"
    protocol._dispatch_event(LID_CLOSED_REPORT)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id("binary_sensor.cover_open")).state == "off"


def test_fill_light_push_changes_only_its_light() -> None:
    protocol = _protocol()
    protocol._latest.update(fill_light_left=100, fill_light_right=100)
    protocol._dispatch_push(FILL_LIGHT_REPORT)
    assert (protocol._latest["fill_light_left"], protocol._latest["fill_light_right"]) == (0, 100)


def test_feeder_sensor_pushes() -> None:
    protocol = _protocol()
    protocol._dispatch_push(_report("/v1/project/device/feeder-sensor", {"frontSensor": "block", "backSensor": "unblock"}))
    assert (protocol._latest["feeder_front_blocked"], protocol._latest["feeder_rear_blocked"]) == (True, False)
    protocol._dispatch_push(_notice("/v1/project/device/feeder-sensor", info={"backSensor": "block"}))
    assert (protocol._latest["feeder_front_blocked"], protocol._latest["feeder_rear_blocked"]) == (True, True)


@pytest.mark.parametrize(("action", "kind"), [("start", "started"), ("cancel", "cancelled"), ("finish", "finished")])
def test_job_events_from_the_process_notices(action: str, kind: str) -> None:
    protocol = _protocol()
    protocol._dispatch_push(_notice("/v1/platform/device/process", content={"action": action, "taskId": "T-7"}))
    assert protocol._pending_events == [("job", kind, {"task_id": "T-7"})]
    assert protocol._latest["task_id"] == "T-7"
    # The same notice again is the same event
    protocol._dispatch_push(_notice("/v1/platform/device/process", content={"action": action, "taskId": "T-7"}))
    assert len(protocol._pending_events) == 1


@pytest.mark.parametrize("method", ["NOTIFY", "NOTIFY-RECORD"])
def test_notices_count_whatever_the_method(method: str) -> None:
    """Studio routes a push by its shape: a list holds notices."""
    protocol = _protocol()
    notice = _notice("/v1/platform/device/process", content={"action": "start", "taskId": "T-8"})
    protocol._dispatch_push({**notice, "method": method})
    assert protocol._pending_events == [("job", "started", {"task_id": "T-8"})]


def test_a_process_event_in_an_object() -> None:
    """Studio's handleEventFromObject: an event path in an object, with its content."""
    protocol = _protocol()
    protocol._dispatch_push(_report("/v1/platform/device/process", {"content": {"action": "finish", "taskId": "T-9"}}))
    assert protocol._pending_events == [("job", "finished", {"task_id": "T-9"})]


def test_other_process_notices_fire_no_event() -> None:
    protocol = _protocol()
    for action in ("ready", "pausing", "pause", "resume"):
        protocol._dispatch_push(_notice("/v1/platform/device/process", content={"action": action, "taskId": "T-7"}))
    protocol._dispatch_push(KEY_NOTICE)
    assert protocol._pending_events == []


# --- Controls: what Studio sends ---------------------------------------


@pytest.mark.parametrize(("domain", "service", "short", "data", "sent"), [
    ("light", "turn_off", "light.fill_light_left", {},
     ("PUT", "/v1/project/peripheral/fill-light", {"name": "left"}, {"brightness": 0})),
    ("light", "turn_on", "light.fill_light_right", {"brightness": 128},
     ("PUT", "/v1/project/peripheral/fill-light", {"name": "right"}, {"brightness": 50})),
    # The close-up light: tenths of a percent, at most Studio's 50 %
    ("light", "turn_on", "light.fill_light_near", {},
     ("PUT", "/v1/project/peripheral/fill-light", {"name": "near"}, {"brightness": 500})),
    ("light", "turn_on", "light.fill_light_near", {"brightness": 51},
     ("PUT", "/v1/project/peripheral/fill-light", {"name": "near"}, {"brightness": 200})),
    ("light", "turn_on", "light.fill_light_near", {"brightness": 255},
     ("PUT", "/v1/project/peripheral/fill-light", {"name": "near"}, {"brightness": 500})),
    ("select", "select_option", "select.exhaust_fan_speed", {"option": "90"},
     ("PUT", "/v1/platform/device/config", None, {"smokeFanSpeed": 900})),
    ("select", "select_option", "select.flame_alarm_sensitivity", {"option": "low"},
     ("PUT", "/v1/platform/device/config", None, {"flameSensitivity": 1})),
    ("switch", "turn_off", "switch.beep_enable", {},
     ("PUT", "/v1/platform/device/config", None, {"beepEnable": False})),
    ("number", "set_value", "number.smoking_fan_duration", {"value": 60},
     ("PUT", "/v1/platform/device/config", None, {"smokeFanTimeout": 60})),
    ("button", "press", "button.pause_job", {},
     ("POST", "/v1/project/device/control", {"action": "PAUSE"}, None)),
    ("button", "press", "button.resume_job", {},
     ("POST", "/v1/project/device/control", {"action": "RESUME"}, None)),
    ("button", "press", "button.cancel_job", {},
     ("POST", "/v1/project/device/control", {"action": "CANCEL"}, None)),
])
async def test_controls_send_what_studio_sends(hass: HomeAssistant, x1, domain, service, short, data, sent) -> None:
    _, protocol = x1
    protocol.sent.clear()
    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: entity_id(short), **data}, blocking=True)
    assert sent in protocol.sent


async def test_a_light_shows_its_new_value(hass: HomeAssistant, x1) -> None:
    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: entity_id("light.fill_light_near"), "brightness": 51}, blocking=True,
    )
    state = hass.states.get(entity_id("light.fill_light_near"))
    assert (state.state, state.attributes["brightness"]) == ("on", 51)
