"""What a test on a real P2 (firmware 40.21.025) showed: baseplate, safety lock, Ethernet."""

from __future__ import annotations


import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator

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


def test_baseplate_is_on_when_in_place(coordinator) -> None:
    """``/peripheral/drawer`` "off" (DRAWER CLOSE) means the baseplate is in place."""
    sensors = {e.translation_key: e for e in coordinator.build_binary_sensors()}
    baseplate = sensors["baseplate"]
    coordinator.data.drawer_open = False
    assert baseplate.is_on is True
    coordinator.data.drawer_open = True
    assert baseplate.is_on is False


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


def test_p2_has_no_reboot_button(coordinator) -> None:
    """The P2 answers /reboot with "invalid request"."""
    assert "reboot" not in {e.translation_key for e in coordinator.build_buttons()}

