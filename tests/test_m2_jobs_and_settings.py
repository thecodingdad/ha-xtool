"""The M2's job events, exhaust fan, settings and Wi-Fi (#8, from rodrigus32's fork).

Firmware 40.141.012 sends the job as ``NOTIFY-RECORD`` notices of
``/v1/platform/device/process``, which Studio's M2 reads in
handleProcessingEvent. The laser reports no time while a job runs (Studio
counts it itself); the statistics' ``singleTime`` is the finished job's
time. Studio's turnOnExhaustFan sends ``{speed: 1000}``, and its
getConfigs a ``kv`` list. The Wi-Fi push is not read by Studio.
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.base import XtoolDeviceState
from custom_components.xtool.protocols.ws_v2.protocol_m2 import M2_CONFIG_KEYS, M2WSV2Protocol

from .conftest import build_coordinator

M2 = DEVICE_MODELS["M2_V2"]
TASK = "c0ffee00-0000-0000-0000-0000000000m2"


class _M2(M2WSV2Protocol):
    def __init__(self) -> None:
        super().__init__("192.0.2.20")
        self.set_model(M2)
        self._connected = True
        self.replies: dict[str, Any] = {}
        self.sent: list[tuple[str, str, Any]] = []

    async def request(self, url, method="GET", params=None, data=None, timeout=10.0, **_):
        self.sent.append((method, url, data))
        return self.replies.get(url, {})


def _process(action: str) -> dict:
    return {
        "type": "request", "method": "NOTIFY-RECORD",
        "data": [{"name": "/v1/platform/device/process", "content": {"action": action, "taskId": TASK}}],
    }


def _jobs(m2: _M2) -> list[str]:
    return [kind for source, kind, _ in m2._pending_events if source == "job"]


def test_the_job_events() -> None:
    m2 = _M2()
    for action in ("ready", "start", "pause", "resume", "finish"):
        m2._dispatch_push(_process(action))
    assert _jobs(m2) == ["started", "finished"]
    assert m2._latest["task_id"] == TASK


async def test_the_finished_jobs_time_from_the_statistics() -> None:
    m2 = _M2()
    m2.replies["/v1/project/process/statistics"] = {"singleTime": "300", "totalTime": "9000", "count": "4"}
    state = XtoolDeviceState()
    await m2._poll_m2_statistics(state)
    assert not state.task_time
    m2._dispatch_push(_process("start"))
    m2._dispatch_push(_process("finish"))
    # Not reported yet: still the job before
    m2._m2_poll_counter = 1
    await m2.poll_state(state)
    assert not state.task_time
    m2.replies["/v1/project/process/statistics"] = {"singleTime": "125", "totalTime": "9125", "count": "5"}
    m2._m2_poll_counter = 1
    await m2.poll_state(state)
    assert (state.task_time, state.last_job_time_seconds) == (125, 125)
    # Done: the statistics are read at their own pace again
    m2.sent.clear()
    m2._m2_poll_counter = 1
    await m2.poll_state(state)
    assert not any(url == "/v1/project/process/statistics" for _, url, _ in m2.sent)


@pytest.mark.parametrize(("action", "speed", "running"), [("on", 1000, True), ("off", 0, False)])
async def test_the_exhaust_fan_as_studio_switches_it(action: str, speed: int, running: bool) -> None:
    m2 = _M2()
    await m2.set_peripheral("smoking_fan", action=action)
    assert m2.sent == [("POST", "/v1/project/peripheral/smokeFan-control", {"speed": speed})]
    assert m2._latest["smoking_fan_running"] is running


async def test_the_settings_are_read_with_studios_keys() -> None:
    m2 = _M2()
    m2.replies["/v1/platform/device/config"] = {"smokeFanTimeout": 30}
    await m2._poll_m2_configs()
    assert m2.sent == [("GET", "/v1/platform/device/config", {"kv": M2_CONFIG_KEYS})]
    assert "smokeFanTimeout" in M2_CONFIG_KEYS and len(M2_CONFIG_KEYS) == 26
    assert m2._latest["smoking_fan_duration"] == 30


def test_the_wifi_push() -> None:
    m2 = _M2()
    m2._dispatch_push({
        "type": "request", "method": "REPORT",
        "data": {"/v1/platform/wifi/info": {"rssiDbm": -61, "ssid": "workshop", "channel": 6, "freq": 2437}},
    })
    state = XtoolDeviceState()
    m2._apply_latest_to_state(state)
    assert (state.wifi_signal, state.wifi_ssid, state.wifi_channel) == (-61, "workshop", 6)


def test_the_wifi_sensors_are_off_until_turned_on(hass: HomeAssistant, config_entry) -> None:
    coordinator = build_coordinator(hass, M2)
    wifi = {e.translation_key: e for e in coordinator.build_sensors() if e.translation_key.startswith("wifi_")}
    assert set(wifi) == {"wifi_signal", "wifi_ssid", "wifi_channel"}
    assert not any(e.entity_registry_enabled_default for e in wifi.values())
