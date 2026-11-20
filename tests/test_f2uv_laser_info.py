"""Tests for the F2 Ultra UV exhaust fan, working time and MAC (issue #4).

The firmware never reports whether the exhaust fan runs; its smoking_fan
read answers whether the fan is installed. Statistics has no working time;
machineInfo's worktime has it. machineInfo's mac is not the Wi-Fi MAC; the
no reply carries the real one.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import requires

WRONG_MAC = "6A:C4:AF:DE:80:A4"


def _model(model_id: str = "F2UltraUV"):
    from custom_components.xtool.protocols import DEVICE_MODELS

    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == model_id and m.protocol_version == "V2"
    )


def _protocol(model_id: str = "F2UltraUV", replies: dict | None = None):
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    proto._model = _model(model_id)
    proto._pending_model = proto._model
    proto._connected = True
    proto.connect = AsyncMock()
    replies = replies or {}

    async def request(url, method="GET", params=None, data=None, **kwargs):
        key = url if not params else f"{url}?{params.get('type')}"
        if key in replies:
            return replies[key]
        raise RuntimeError(f"V2 {method} {url} returned code 1: failed")

    proto.request = request
    return proto


def _state(**fields):
    from custom_components.xtool.protocols.base import XtoolDeviceState

    return XtoolDeviceState(**fields)


# ── Exhaust fan ─────────────────────────────────────────────────────


@requires("exhaust_fan_install_state")
@pytest.mark.parametrize(("reply", "installed"), [("install", True), ("uninstall", False)])
async def test_install_state_is_read_and_not_taken_as_running(reply: str, installed: bool) -> None:
    proto = _protocol(replies={"/v1/peripheral/param?smoking_fan": {"state": reply}})
    proto._assume_smoking_fan_off = False
    state = _state(smoking_fan_running=True)
    await proto.poll_state(state)
    assert state.smoking_fan_installed is installed
    assert state.smoking_fan_running is True


@requires("exhaust_fan_install_state")
@pytest.mark.parametrize(("typ", "installed"), [("DEVICE_ADD", True), ("DEVICE_REMOVE", False)])
def test_install_push(typ: str, installed: bool) -> None:
    proto = _protocol()
    proto._dispatch_push({"url": "/smoking_fan/status", "data": {"module": "SMOKING_FAN", "type": typ, "info": ""}})
    assert proto._latest["smoking_fan_installed"] is installed


@requires("exhaust_fan_install_state")
async def test_exhaust_fan_is_assumed_off_once_per_connection() -> None:
    """Off after a new connection, then HA's own command stands."""
    proto = _protocol()
    proto._assume_smoking_fan_off = True  # set by connect()
    state = _state(smoking_fan_running=True)
    await proto.poll_state(state)
    assert state.smoking_fan_running is False

    state.smoking_fan_running = True  # HA turned it on
    await proto.poll_state(state)
    assert state.smoking_fan_running is True


@requires("exhaust_fan_install_state")
def test_problem_sensor_replaces_running_sensor(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    coordinator = build_coordinator(hass, _model())
    keys = {e.translation_key for e in coordinator.build_binary_sensors()}
    assert "smoking_fan_installation" in keys
    assert "smoking_fan_running" not in keys

    sensor = next(
        e for e in coordinator.build_binary_sensors()
        if e.translation_key == "smoking_fan_installation"
    )
    coordinator.data = _state(smoking_fan_installed=False)
    assert sensor.is_on is True
    coordinator.data = _state(smoking_fan_installed=True)
    assert sensor.is_on is False


@requires("exhaust_fan_install_state")
def test_other_models_keep_the_running_sensor(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    for model in DEVICE_MODELS.values():
        if model.has_smoking_fan and not model.has_smoking_fan_install_state and model.protocol_version == "V2":
            coordinator = build_coordinator(hass, model)
            keys = {e.translation_key for e in coordinator.build_binary_sensors()}
            assert "smoking_fan_running" in keys, model.model_id


# ── Working time ────────────────────────────────────────────────────

COUNTS_ONLY = {"numOnlineWorking": 409, "numOfflineWorking": 161}


@requires("working_time_from_machine_info")
async def test_working_time_from_machine_info() -> None:
    proto = _protocol(replies={
        "/v1/device/statistics": COUNTS_ONLY,
        "/v1/device/machineInfo": {"worktime": 70922, "worktimes": 570},
    })
    state = _state()
    await proto.poll_state(state)
    assert state.working_seconds == 70922
    assert state.session_count == 570


@requires("working_time_from_machine_info")
async def test_statistics_time_wins() -> None:
    proto = _protocol(replies={
        "/v1/device/statistics": {"timeModeWorking": 100, **COUNTS_ONLY},
        "/v1/device/machineInfo": {"worktime": 70922},
    })
    state = _state()
    await proto.poll_state(state)
    assert state.working_seconds == 100
    proto._dispatch_push({"url": "/device/info", "data": {"module": "MACHINE_INFO", "type": "INFO", "info": {"worktime": 5}}})
    assert proto._latest["working_seconds"] == 100


@requires("working_time_from_machine_info")
def test_working_time_sensor_exists(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    coordinator = build_coordinator(hass, _model())
    keys = [e.entity_description.key for e in coordinator.build_sensors()]
    assert keys.count("working_seconds") == 1
    sensor = next(e for e in coordinator.build_sensors() if e.entity_description.key == "working_seconds")
    # Raises when HA cannot convert to the suggested unit.
    assert sensor.unit_of_measurement in ("h", "s")


# ── MAC ─────────────────────────────────────────────────────────────

MACHINE_INFO = {"mac": WRONG_MAC, "sn": "MXTEST0001", "firmware": {"package_version": "40.130.023.00.ht5"}}


@requires("no_wrong_mac")
async def test_f2_ultra_uv_shows_no_mac() -> None:
    proto = _protocol(replies={"/v1/device/machineInfo": MACHINE_INFO})
    proto._dispatch_push({"url": "/device/info", "data": {"module": "MACHINE_INFO", "type": "INFO", "info": MACHINE_INFO}})
    info = await proto.get_device_info()
    assert info.mac_address == ""


@requires("no_wrong_mac")
async def test_other_models_keep_machine_info_mac() -> None:
    proto = _protocol("F1Ultra", replies={"/v1/device/machineInfo": MACHINE_INFO})
    info = await proto.get_device_info()
    assert info.mac_address == WRONG_MAC


@requires("no_wrong_mac")
def test_wrong_mac_is_removed_from_the_device(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    from custom_components.xtool import _drop_mac_connections

    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("xtool", "MXTEST0001")},
        connections={(dr.CONNECTION_NETWORK_MAC, WRONG_MAC)},
    )
    _drop_mac_connections(hass, device)
    assert registry.async_get(device.id).connections == set()
