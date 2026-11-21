"""The xTool X1 (JS001), from Studio's X1 extension and the replies an X1
owner captured in PR #61.

The X1 shares the M2's platform 2.0 interface, not its hardware: none of
the M2's inkjet, exhaust fan or air pump entities.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, call

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xtool.const import XtoolStatus
from custom_components.xtool.protocols import DEVICE_MODELS, detect_models
from custom_components.xtool.protocols.base import XtoolDeviceState
from custom_components.xtool.protocols.ws_v2.protocol_x1 import X1WSV2Protocol

from .conftest import build_coordinator

X1 = DEVICE_MODELS["X1_V2"]

# PR #61: the state push an idle X1 sends after /state/sync.
IDLE_REPORT = {"type": "request", "method": "REPORT", "data": {
    "/v1/platform/device/state": {
        "mode": "Idle", "moveState": "idle", "gohomeState": "finish", "processInfo": None,
    },
}}


def _protocol(replies: dict[str, object] | None = None) -> X1WSV2Protocol:
    protocol = X1WSV2Protocol("192.0.2.12")
    protocol.set_model(X1)
    protocol._connected = True
    answers = replies or {}

    async def request(url, method="GET", **kwargs):
        return answers.get(url, {})

    protocol.request = AsyncMock(side_effect=request)
    return protocol


def _report(url: str, payload: dict) -> dict:
    return {"type": "request", "method": "REPORT", "data": {url: payload}}


def test_found_by_its_discovery_name() -> None:
    for name in ("xTool_X1_example", "JS001"):
        assert [m for m in detect_models(name) if m.protocol_version == "V2"] == [X1]


@pytest.mark.parametrize(("mode", "status"), [
    ("Idle", XtoolStatus.IDLE),
    ("WorkPlay", XtoolStatus.PROCESSING),
    ("WorkPause", XtoolStatus.PAUSED),
    ("Sleep", XtoolStatus.SLEEPING),
    ("Leveling", XtoolStatus.MEASURING),
    ("Busy", XtoolStatus.WORKING_API),
])
def test_status_from_the_state_push(mode: str, status: XtoolStatus) -> None:
    protocol = _protocol()
    protocol._dispatch_push(_report("/v1/platform/device/state", {"mode": mode, "taskId": "T-1"}))
    assert protocol._latest["status"] is status
    assert protocol._latest["task_id"] == "T-1"


def test_captured_idle_report() -> None:
    protocol = _protocol()
    protocol._dispatch_push(IDLE_REPORT)
    assert protocol._latest["status"] is XtoolStatus.IDLE


async def test_poll_reads_lid_safety_key_and_statistics() -> None:
    protocol = _protocol({
        "/v1/project/peripheral/lid-status": {"lidStatus": True},
        "/v1/project/peripheral/usbkey": {"usbKey": False},
        "/v1/project/process/statistics": {"totalTime": "7200", "standbyTime": 3600, "count": 12},
        "/v1/platform/device/config": {"smokeFanTimeout": 30},
    })
    state = XtoolDeviceState()
    await protocol.poll_state(state)
    urls = [c.args[0] for c in protocol.request.await_args_list]
    assert urls[0] == "/v1/platform/device/state/sync"
    assert state.cover_open is True
    assert state.machine_lock is False
    assert (state.working_seconds, state.standby_seconds, state.session_count) == (7200, 3600, 12)
    assert call("/v1/platform/device/config", "GET", data={"kv": ["smokeFanTimeout"]}) in protocol.request.await_args_list
    assert state.smoking_fan_duration == 30
    # No M2 hardware is read.
    assert not any("inkjet" in u or "smokeFan" in u or "airPump" in u for u in urls)


def test_lid_and_safety_key_pushes() -> None:
    protocol = _protocol()
    protocol._dispatch_push(_report("/v1/project/peripheral/lid", {"state": False}))
    protocol._dispatch_push(_report("/v1/project/peripheral/usbkey", {"state": True}))
    assert protocol._latest["cover_open"] is False
    assert protocol._latest["machine_lock"] is True


@pytest.mark.parametrize(("action", "sent"), [("pause", "PAUSE"), ("start", "RESUME"), ("stop", "CANCEL")])
async def test_job_control_as_studio_sends_it(action: str, sent: str) -> None:
    protocol = _protocol()
    await protocol.set_processing_state(action)
    assert protocol.request.await_args == call(
        "/v1/project/device/control", method="POST", params={"action": sent},
    )


async def test_exhaust_post_run_written_as_studio_does() -> None:
    protocol = _protocol()
    await protocol.set_config("purifierTimeout", 90)
    assert protocol.request.await_args == call(
        "/v1/platform/device/config", "PUT", data={"smokeFanTimeout": 90},
    )


async def test_no_peripheral_controls_yet() -> None:
    with pytest.raises(NotImplementedError):
        await _protocol().set_peripheral("fill_light", "set_brightness", value=10)


def _keys(entities) -> set[str]:
    return {e.unique_id.split("_", 1)[1] for e in entities}


def test_entities(hass: HomeAssistant, config_entry) -> None:
    coordinator = build_coordinator(hass, X1)
    assert _keys(coordinator.build_cameras()) == {"camera_far", "camera_near", "camera_side"}
    assert {"cover_open", "machine_lock"} <= _keys(coordinator.build_binary_sensors())
    assert {"pause_job", "resume_job", "cancel_job"} <= _keys(coordinator.build_buttons())
    assert {"working_time", "session_count", "standby_time"} & _keys(coordinator.build_sensors())
    everything = set().union(*(
        _keys(build()) for build in (
            coordinator.build_sensors, coordinator.build_binary_sensors, coordinator.build_switches,
            coordinator.build_numbers, coordinator.build_lights, coordinator.build_buttons,
        )
    ))
    assert "smoking_fan_duration" in everything  # Studio's "Exhaust time"
    assert not {k for k in everything if "inkjet" in k or "air_assist" in k}
    assert "smoking_fan" not in everything
    assert "stops_when_moved" not in everything
    assert not coordinator.build_lights()
