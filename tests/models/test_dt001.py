"""The xTool Apparel Printer (DT001), as a real one behaves.

Built from what an Apparel Printer on firmware 40.100.028.00.ht6 with an
OS1 oven answered (see dt001_capture.py), and checked against Studio's
DT001 extension (Studio 1.11.9): its modes, lid names, bottle states,
oven route, maintenance reminders and collision alarm. The busy-flag bits
for the oven are only what the printer showed; Studio does not read them.
"""

from __future__ import annotations

import pytest

from custom_components.xtool.const import XtoolStatus
from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.base import XtoolDeviceState
from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

from .dt001_capture import CAPTURE, FakeDT001

DT001 = DEVICE_MODELS["DT001_V2"]
LIDS = ("top_lid_open", "left_lid_open", "right_lid_open")


def _printer() -> FakeDT001:
    printer = FakeDT001()
    printer.set_model(DT001)
    return printer


def _state(printer: FakeDT001) -> XtoolDeviceState:
    state = XtoolDeviceState()
    printer._apply_latest_to_state(state)
    return state


# --- The poll --------------------------------------------------------------------


async def test_the_captured_poll() -> None:
    printer = _printer()
    state = XtoolDeviceState()
    await printer.poll_state(state)
    assert state.cpu_temp == 38  # °C on this printer, not thousandths
    assert state.ambient_humidity == 46
    assert state.ink_white == "normal"
    assert (state.clean_water, state.waste_water, state.waste_tank_full) == ("normal", "normal", False)
    assert (state.heater_connected, state.heater_temperature) == (True, 84)
    assert [getattr(state, lid) for lid in LIDS] == [False, False, False]
    assert (state.oven_processing, state.oven_feed_active) == (False, False)
    assert state.session_count == 61
    assert state.printer_maintenance["numKnifeCut"] == 97
    assert state.printer_maintenance["bumpExceptions"] == 33
    assert state.wiping_maintenance_due is True
    assert state.fitting_replacement_due is False
    assert all(method == "GET" for method, _ in printer.sent)


async def test_without_the_oven_route() -> None:
    printer = _printer()
    printer.replies["/v1/device/roaster"] = RuntimeError("Device error code 404: missing")
    state = XtoolDeviceState()
    await printer.poll_state(state)
    await printer.poll_state(state)
    assert state.heater_temperature is None
    assert state.ink_white == "normal"
    assert [url for _, url in printer.sent].count("/v1/device/roaster") == 1


async def test_the_oven_switched_off() -> None:
    printer = _printer()
    printer._apply_busy_state({"deviceBusyState": {"busyFlag": 262144}})
    printer.replies["/v1/device/roaster"] = {"connectState": "Disconnected", "heaterTemp": 0, "heatingStatus": 0}
    state = XtoolDeviceState()
    await printer.poll_state(state)
    assert state.heater_connected is False
    assert (state.heater_temperature, state.heating_status, state.oven_processing) == (None, None, None)
    assert state.waste_water == "normal"


# --- Modes -----------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "sub_mode", "status"), [
    ("P_CLEAN", "", XtoolStatus.CLEANING),
    ("P_CALIBRATIPON", "", XtoolStatus.CALIBRATING),
    ("Work", "workReady", XtoolStatus.PROCESSING_READY),
    ("Work", "working", XtoolStatus.PROCESSING),
    ("Work", "workPause", XtoolStatus.PAUSED),
    ("P_IDLE", "", XtoolStatus.IDLE),
])
async def test_studios_modes(mode: str, sub_mode: str, status: XtoolStatus) -> None:
    info = {"mode": mode, "subMode": sub_mode}
    printer = _printer()
    printer.replies["/v1/device/runtime-infos"] = {"curMode": info}
    state = XtoolDeviceState()
    await printer._poll_runtime_status(state)
    assert state.status is status
    pushed = _printer()
    pushed._dispatch_push({"url": "/work/mode", "data": {
        "module": "STATUS_CONTROLLER", "type": "MODE_CHANGE", "info": info,
    }})
    assert _state(pushed).status is status


# --- Lids ------------------------------------------------------------------------


async def test_the_lids_as_polled_while_opened_and_closed() -> None:
    printer = _printer()
    for observation in CAPTURE["panel_physical_transitions"]:
        printer.replies["/v1/device/runtime-infos"] = observation["data"]
        state = XtoolDeviceState()
        await printer._poll_runtime_status(state)
        assert [getattr(state, lid) for lid in LIDS] == [
            bool(value) for value in observation["data"]["gapState"][:3]
        ]


def test_the_lid_pushes_have_no_url() -> None:
    printer = _printer()
    printer._apply_panel_states(XtoolDeviceState(), {"gapState": [0, 0, 0, 0]})
    expected = dict.fromkeys(LIDS, False)
    for record in CAPTURE["panel_push_events"]:
        event = record["event"]
        assert event["url"] == ""
        expected[f"{event['data']['info'].removeprefix('gap_')}_lid_open"] = event["data"]["type"] == "OPEN"
        printer._dispatch_push(event)
        state = _state(printer)
        assert {lid: getattr(state, lid) for lid in LIDS} == expected


def test_missing_lid_data_is_not_closed() -> None:
    printer = _printer()
    state = XtoolDeviceState()
    printer._apply_panel_states(state, {"gapState": [1, 0, 0, 0]})
    assert state.top_lid_open is True
    printer._apply_panel_states(state, {"gapState": [None, 0, 0, 0]})
    assert state.top_lid_open is None


# --- Bottles ---------------------------------------------------------------------


def test_the_waste_tank_as_emptied_and_taken_out() -> None:
    printer = _printer()
    state = XtoolDeviceState()
    full = {"full": True, "normal": False, "detach": None}
    for observation in CAPTURE["waste_tank_physical_transitions"]:
        printer._apply_water_bottle(state, {"waste_water_bottle": observation["waste_water"]})
        assert state.waste_water == observation["waste_water"]
        assert state.waste_tank_full is full[observation["waste_water"]]


def test_the_full_waste_tank_from_the_status_push() -> None:
    printer = _printer()
    for value, full in (("full", True), ("normal", False)):
        printer._dispatch_push({"url": "/device/status", "data": {
            "module": "DEVICE_STATUS", "type": "INFO",
            "info": {"waterBottle": {"waste_water_bottle": value}},
        }})
        assert _state(printer).waste_tank_full is full


@pytest.mark.parametrize("value", [None, "", "future-state", 0])
def test_a_bottle_state_studio_does_not_know_clears_it(value) -> None:
    printer = _printer()
    state = XtoolDeviceState()
    printer._apply_water_bottle(state, {"waste_water_bottle": "full", "clean_water_bottle": "normal"})
    printer._apply_ink_bottle(state, {"ink_w": "normal"})
    printer._apply_water_bottle(state, {})
    assert (state.waste_tank_full, state.clean_water) == (True, "normal")
    printer._apply_water_bottle(state, {"waste_water_bottle": value, "clean_water_bottle": value})
    printer._apply_ink_bottle(state, {"ink_w": value})
    assert (state.waste_water, state.waste_tank_full, state.clean_water, state.ink_white) == (None, None, None, None)


# --- Oven ------------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "flag", "expected"), [
    ("Work", 262145, (XtoolStatus.PROCESSING, True, False)),
    # The oven goes on baking after the print
    ("P_IDLE", 262144, (XtoolStatus.IDLE, True, False)),
    ("P_IDLE", 32768, (XtoolStatus.IDLE, False, True)),
    ("P_IDLE", 0, (XtoolStatus.IDLE, False, False)),
])
async def test_the_oven_busy_flags(mode: str, flag: int, expected: tuple) -> None:
    printer = _printer()
    printer.replies["/v1/device/runtime-infos"] = {
        "curMode": {"mode": mode, "subMode": "working" if mode == "Work" else ""},
        "deviceBusyState": {"busyFlag": flag},
    }
    state = XtoolDeviceState()
    await printer._poll_runtime_status(state)
    state = _state(printer)
    assert (state.status, state.oven_processing, state.oven_feed_active) == expected


async def test_a_nozzle_clean_and_an_oven_cycle_as_captured() -> None:
    printer = _printer()
    for record in CAPTURE["oven_and_cleaning_physical_transitions"]:
        printer.replies["/v1/device/runtime-infos"] = record["runtime"]
        await printer._poll_runtime_status(XtoolDeviceState())
        state = _state(printer)
        for key, value in record["expected"].items():
            assert getattr(state, key) == value, (record["observed_utc"], key)


# --- Job time, maintenance, collision --------------------------------------------


def test_the_print_time_during_a_print() -> None:
    printer = _printer()
    for event in CAPTURE["print_elapsed_pushes"]:
        printer._dispatch_push(event)
        assert _state(printer).task_time == event["data"]["info"]["processingTime"]


@pytest.mark.parametrize(("config", "wiping_due"), [
    ({"cleanRemindDay": 3, "worktimeFromLastCleanMatain": 3 * 86400}, True),
    ({"cleanRemindDay": 3, "worktimeFromLastCleanMatain": 3 * 86400 - 1}, False),
    # -1: Studio's No reminder
    ({"cleanRemindDay": -1, "worktimeFromLastCleanMatain": 10**7}, False),
])
def test_studios_wiping_reminder(config: dict, wiping_due: bool) -> None:
    printer = _printer()
    printer._apply_maintenance(config)
    assert printer._latest["wiping_maintenance_due"] is wiping_due


def test_no_reminder_without_its_data() -> None:
    printer = _printer()
    printer._apply_maintenance({})
    assert "wiping_maintenance_due" not in printer._latest


@pytest.mark.parametrize("url", ["", "/unexpected/route"])
def test_a_collision_push_fires_the_error_event(url: str) -> None:
    printer = _printer()
    printer._dispatch_push({"url": url, "data": {
        "module": "HEAD_COLLISION", "type": "CLOSE", "level": "error", "info": "2",
    }})
    assert printer._pending_events == [("error", "print_head_collision", {"raw_type": "CLOSE", "info": "2"})]


async def test_an_old_collision_in_the_alarm_list_fires_nothing() -> None:
    printer = _printer()
    printer.replies["/v1/device/runtime-infos"] = CAPTURE["collision_simulation_recovery"]["runtime"]
    await printer._poll_runtime_status(XtoolDeviceState())
    assert printer._pending_events == []


# --- The lasers keep their own units ---------------------------------------------


async def test_the_lasers_cpu_temperature_stays_in_thousandths() -> None:
    laser = WSV2Protocol("192.0.2.42")

    async def reply(*_args, **_kwargs):
        return {"cpuTemp": 43565}

    laser.request = reply
    state = XtoolDeviceState()
    await laser._poll_runtime_status(state)
    assert state.cpu_temp == 43
