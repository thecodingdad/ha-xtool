"""The P2's device page: safety class as Model ID, both MACs, its two addresses."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import COMPONENT, has, requires

pytestmark = requires("p2_device_info")

MACHINE_INFO = {
    "deviceName": "P2", "sn": "MXTEST0001", "mac": "48:FB:A9:5C:4F:7D", "deviceCode": "MP2",
    "machineSubType": "004", "laserPower": 55,
    "ip": {"eth0-0-ip": "169.254.2.1", "usb0-ip": "201.234.3.1", "wlan0-ip": "192.0.2.47"},
    "firmware": {"package_version": "40.21.025.2020.01.ht3"}, "hardware": {"h3": "V1"},
}


def p2():
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    return P2Protocol("192.0.2.47")


async def test_identity_from_machine_info(monkeypatch) -> None:
    """Studio's class is the Model ID; the laser's own addresses are known."""
    proto = p2()

    async def get_json(path, timeout=5.0):
        return MACHINE_INFO

    monkeypatch.setattr(proto, "_get_json", get_json)
    info = await proto.get_device_info()
    assert info.serial_number == "MXTEST0001"
    assert proto.model_variant() == "Class 4"
    if not has("address_by_discovery"):
        # The unplugged cable (169.254) and the USB link are not addresses to use
        assert proto.addresses() == ["192.0.2.47"]
        proto.machine_info = {**MACHINE_INFO, "ip": {"eth0-0-ip": "192.0.2.46", "wlan0-ip": "192.0.2.47"}}
        assert proto.addresses() == ["192.0.2.46", "192.0.2.47"]


async def test_work_info_as_studio_reads_it(monkeypatch) -> None:
    """Working time, Operating times and Standby time from /device/workingInfo."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    proto = p2()
    work = {"fireboxV1_5Used": False, "numOfflineWorking": 29, "numOnlineWorking": 651,
            "timeModeWorking": 200267, "timeSystemWork": 984300}

    async def get_json(path, timeout=5.0):
        return work if path == "/device/workingInfo" else None

    async def post_json(path, data=None, timeout=5.0):
        return None

    monkeypatch.setattr(proto, "_get_json", get_json)
    monkeypatch.setattr(proto, "_post_json", post_json)
    state = XtoolDeviceState()
    await proto._read_slow(state)
    assert (state.working_seconds, state.session_count, state.standby_seconds) == (200267, 680, 984300)


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.protocols.rest import XTOOL_P2

    coord = build_coordinator(hass, XTOOL_P2)
    coord.config_entry = config_entry
    return coord


def test_device_page_shows_class_and_both_macs(coordinator) -> None:
    """Model ID is the safety class; the Ethernet and Wi-Fi MACs are both connections."""
    coordinator.mac_address = "02:00:00:00:00:01"
    coordinator.other_mac_addresses = {"02:00:00:00:00:02"}
    coordinator.model_variant = "Class 4"
    entity = coordinator.build_sensors()[0]
    info = entity.device_info
    assert info["model_id"] == "Class 4"
    assert {mac for _, mac in info["connections"]} == {"02:00:00:00:00:01", "02:00:00:00:00:02"}


@pytest.mark.skipif(has("address_by_discovery"), reason="replaced by the discovery search (test_address_discovery)")
async def test_finds_the_laser_at_its_other_address(coordinator, monkeypatch) -> None:
    """When the configured address stops answering, the other one is tried."""
    from custom_components.xtool.protocols.rest import coordinator as rest_coordinator

    coordinator.protocol.host = "192.0.2.46"
    coordinator._known_hosts = ["192.0.2.46", "192.0.2.47"]
    asked = []

    async def serial_at(host):
        asked.append(host)
        return "MXTEST0001" if host == "192.0.2.47" else ""

    monkeypatch.setattr(rest_coordinator, "_serial_at", serial_at)
    coordinator.protocol.change_host = AsyncMock()
    update = MagicMock()
    monkeypatch.setattr(coordinator.hass.config_entries, "async_update_entry", update)
    monkeypatch.setattr(coordinator.hass.config_entries, "async_schedule_reload", MagicMock())
    await coordinator._find_on_other_address()
    assert asked == ["192.0.2.47"]
    assert update.call_args.kwargs["data"]["host"] == "192.0.2.47"
    # Not again within the next 30 seconds
    update.reset_mock()
    await coordinator._find_on_other_address()
    update.assert_not_called()


@pytest.mark.skipif(has("address_by_discovery"), reason="replaced by the discovery search (test_address_discovery)")
async def test_a_different_laser_is_not_taken(coordinator, monkeypatch) -> None:
    """Another laser answering at the other address is left alone."""
    from custom_components.xtool.protocols.rest import coordinator as rest_coordinator

    coordinator.protocol.host = "192.0.2.46"
    coordinator._known_hosts = ["192.0.2.46", "192.0.2.47"]
    monkeypatch.setattr(rest_coordinator, "_serial_at", AsyncMock(return_value="OTHER"))
    update = MagicMock()
    monkeypatch.setattr(coordinator.hass.config_entries, "async_update_entry", update)
    monkeypatch.setattr(coordinator.hass.config_entries, "async_schedule_reload", MagicMock())
    await coordinator._find_on_other_address()
    update.assert_not_called()


def test_dhcp_finds_registered_lasers() -> None:
    """DHCP discovery watches the MACs of the lasers already set up."""
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert {"registered_devices": True} in manifest.get("dhcp", [])
