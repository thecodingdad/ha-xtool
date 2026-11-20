"""What a test on a real P2 (firmware 40.21.025) showed: baseplate, safety lock, Ethernet."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import has, requires

MACHINE_INFO = {
    "deviceName": "P2", "sn": "MXTEST0001", "mac": "48:FB:A9:5C:4F:7D", "machineSubType": "004",
    "ip": {"eth0-0-ip": "169.254.2.1", "eth0-ip": "192.0.2.46", "usb0-ip": "201.234.3.1",
           "wlan0-ip": "192.0.2.47"},
}


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest import XTOOL_P2

    coord = build_coordinator(hass, XTOOL_P2)
    coord.config_entry = config_entry
    coord.serial_number = "MXTEST0001"
    state = XtoolDeviceState()
    state.available = True
    coord.data = state
    return coord


@requires("p2_hardware_findings")
def test_baseplate_is_on_when_in_place(coordinator) -> None:
    """``/peripheral/drawer`` "off" (DRAWER CLOSE) means the baseplate is in place."""
    sensors = {e.translation_key: e for e in coordinator.build_binary_sensors()}
    baseplate = sensors["baseplate"]
    coordinator.data.drawer_open = False
    assert baseplate.is_on is True
    coordinator.data.drawer_open = True
    assert baseplate.is_on is False


@requires("p2_hardware_findings")
async def test_safety_lock_sends_on_and_off(monkeypatch) -> None:
    """The P2 refuses "lock" and "unlock"; it takes "on" and "off"."""
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("192.0.2.47")
    sent = []

    async def post(path, data=None, **kwargs):
        sent.append((path, data))

    monkeypatch.setattr(proto, "_post", post)
    await proto.set_digital_lock(True)
    await proto.set_digital_lock(False)
    assert sent == [("/peripheral/digital_lock", {"action": "on"}),
                    ("/peripheral/digital_lock", {"action": "off"})]


@requires("p2_ethernet")
def test_ethernet_address_and_connection() -> None:
    """eth0-ip is listed only with a cable; Connection follows the address in use."""
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("192.0.2.47")
    proto.machine_info = MACHINE_INFO
    assert proto.ethernet_address() == "192.0.2.46"
    assert proto.connection() == "wifi"
    proto.host = "192.0.2.46"
    assert proto.connection() == "ethernet"
    proto.machine_info = {**MACHINE_INFO, "ip": {"eth0-0-ip": "169.254.2.1", "wlan0-ip": "192.0.2.47"}}
    assert proto.ethernet_address() == ""


@requires("p2_ethernet")
@pytest.mark.skipif(has("address_by_discovery"), reason="replaced by the discovery search (test_address_discovery)")
async def test_moves_to_ethernet_when_it_answers(coordinator, monkeypatch) -> None:
    """On Wi-Fi with a cable connected, the entry moves to the Ethernet address."""
    from custom_components.xtool.protocols.rest import coordinator as rest_coordinator

    coordinator.protocol.host = "192.0.2.47"
    coordinator.protocol.ethernet_address = MagicMock(return_value="192.0.2.46")
    coordinator.protocol.change_host = AsyncMock()
    update = MagicMock()
    monkeypatch.setattr(coordinator.hass.config_entries, "async_update_entry", update)
    reload = MagicMock()
    monkeypatch.setattr(coordinator.hass.config_entries, "async_schedule_reload", reload)

    monkeypatch.setattr(rest_coordinator, "_serial_at", AsyncMock(return_value="OTHER"))
    await coordinator._prefer_ethernet()
    update.assert_not_called()

    monkeypatch.setattr(rest_coordinator, "_serial_at", AsyncMock(return_value="MXTEST0001"))
    await coordinator._prefer_ethernet()
    assert update.call_args.kwargs["data"]["host"] == "192.0.2.46"

    # Already on Ethernet: nothing to do
    update.reset_mock()
    coordinator.protocol.host = "192.0.2.46"
    await coordinator._prefer_ethernet()
    update.assert_not_called()


@requires("p2_ethernet")
async def test_discovery_keeps_an_address_that_answers(hass: HomeAssistant, monkeypatch) -> None:
    """Seeing the laser at its Wi-Fi address does not move it off a working Ethernet one."""
    from custom_components.xtool import config_flow
    from custom_components.xtool.const import DOMAIN
    from custom_components.xtool.protocols.base import ConnectionInfo

    entry = MockConfigEntry(domain=DOMAIN, unique_id="MXTEST0002", data={"host": "192.0.2.46"})
    entry.add_to_hass(hass)
    answering = {"192.0.2.46"}

    async def validate(host):
        return ConnectionInfo(host=host, serial_number="MXTEST0002") if host in answering else "cannot_connect"

    monkeypatch.setattr(config_flow, "validate_connection", validate)
    flow = config_flow.XtoolConfigFlow()
    flow.hass = hass
    assert await flow._configured_host_answers("MXTEST0002", "192.0.2.47") is True
    answering.clear()
    assert await flow._configured_host_answers("MXTEST0002", "192.0.2.47") is False


@requires("p2_sleep_photo")
@pytest.mark.skipif(has("p2_cameras"), reason="P2 cameras are P2Camera (test_p2_cameras)")
async def test_no_photo_while_sleeping(coordinator, monkeypatch) -> None:
    """A photo wakes a sleeping P2, so the camera keeps its last one."""
    from custom_components.xtool.const import XtoolStatus
    from custom_components.xtool.protocols.rest import entities

    camera = next(e for e in coordinator.build_cameras() if isinstance(e, entities.XtoolCamera))
    camera._last_image = b"old"
    camera._last_fetch = camera._last_fetch.replace(year=2000)
    coordinator.data.status = XtoolStatus.SLEEPING

    def no_session(*args, **kwargs):
        raise AssertionError("a photo was requested while asleep")

    monkeypatch.setattr(entities.aiohttp, "ClientSession", no_session)
    assert await camera.async_camera_image() == b"old"


@requires("p2_move_without_reload")
async def test_a_move_keeps_the_entities(coordinator, monkeypatch) -> None:
    """A new address is used at once and saved; the entry is not reloaded."""
    from custom_components.xtool import _async_options_updated
    from custom_components.xtool.protocols.rest import coordinator as rest_coordinator

    coordinator.protocol.host = "192.0.2.46"
    coordinator.protocol.ethernet_address = MagicMock(return_value="192.0.2.244")
    coordinator.protocol.change_host = AsyncMock()
    monkeypatch.setattr(rest_coordinator, "_serial_at", AsyncMock(return_value="MXTEST0001"))
    reload = AsyncMock()
    monkeypatch.setattr(coordinator.hass.config_entries, "async_reload", reload)
    entry = coordinator.config_entry
    entry.runtime_data = coordinator

    await coordinator._prefer_ethernet()
    coordinator.protocol.change_host.assert_awaited_once_with("192.0.2.244")
    assert entry.data["host"] == "192.0.2.244"
    # The update listener sees the coordinator's own change and skips the reload
    await _async_options_updated(coordinator.hass, entry)
    reload.assert_not_awaited()

    # Any other change still reloads
    coordinator.hass.config_entries.async_update_entry(entry, options={**entry.options, "x": 1})
    await _async_options_updated(coordinator.hass, entry)
    reload.assert_awaited_once()


@requires("p2_sensor_cleanup")
def test_p2_leaves_out_sensors_that_tell_nothing(coordinator) -> None:
    """No tool type, working mode, gyro, laser power or module; some sensors start off."""
    sensors = {e.translation_key: e for e in coordinator.build_sensors()}
    for key in ("hardware_type", "print_tool_type", "working_mode", "gyro_x", "gyro_y", "gyro_z",
                "laser_power", "laser_module"):
        assert key not in sensors
    assert sensors["task_id"].entity_registry_enabled_default is False
    assert sensors["cpu_temp"].entity_registry_enabled_default is False
    assert sensors["task_progress"].entity_registry_enabled_default is True
    binaries = {e.translation_key: e for e in coordinator.build_binary_sensors()}
    assert binaries["cooling_fan_running"].entity_registry_enabled_default is False
    assert binaries["water_pump_running"].entity_registry_enabled_default is False
    assert binaries["smoking_fan_running"].entity_registry_enabled_default is True


@requires("p2_no_reboot")
def test_p2_has_no_reboot_button(coordinator) -> None:
    """The P2 answers /reboot with "invalid request"."""
    assert "reboot" not in {e.translation_key for e in coordinator.build_buttons()}


@requires("p2_back_to_ethernet")
async def test_wifi_tries_ethernet_again_every_few_minutes(coordinator) -> None:
    """A few lost replies move it to Wi-Fi; it does not stay there."""
    from custom_components.xtool.protocols.rest import coordinator as rest_coordinator

    coordinator._prefer_ethernet = AsyncMock()
    await coordinator._back_to_ethernet()
    await coordinator._back_to_ethernet()
    coordinator._prefer_ethernet.assert_awaited_once()
    coordinator._next_ethernet_check = 0.0
    await coordinator._back_to_ethernet()
    assert coordinator._prefer_ethernet.await_count == 2
    assert 60 <= rest_coordinator.ETHERNET_CHECK_SECONDS <= 900


@requires("p2_back_to_ethernet")
async def test_a_poll_that_answers_checks_ethernet(coordinator, monkeypatch) -> None:
    from custom_components.xtool.protocols.rest import coordinator as rest_coordinator

    state = coordinator.data
    monkeypatch.setattr(rest_coordinator.RestCoordinator, "_async_update_data", AsyncMock(return_value=state))
    coordinator._back_to_ethernet = AsyncMock()
    coordinator._find_on_other_address = AsyncMock()
    await coordinator._async_update_data()
    coordinator._back_to_ethernet.assert_awaited_once()
    state.available = False
    await coordinator._async_update_data()
    coordinator._back_to_ethernet.assert_awaited_once()
    coordinator._find_on_other_address.assert_awaited_once()
