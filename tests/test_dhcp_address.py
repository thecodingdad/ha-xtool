"""A laser already set up follows its new address when it asks for one (DHCP).

Seen on a P2: after power on it asked for an address while the integration
still used the one from the last start; the integration found the new one
only by its own search, a minute or more later.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .features import has, requires

pytestmark = requires("dhcp_follows_mac")

SERIAL = "MXTEST0003"
ETHERNET_MAC = "02:00:00:00:00:01"
WIFI_MAC = "02:00:00:00:00:02"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    from custom_components.xtool.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"host": "192.0.2.244"})
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, SERIAL)},
        connections={(dr.CONNECTION_NETWORK_MAC, ETHERNET_MAC), (dr.CONNECTION_NETWORK_MAC, WIFI_MAC)},
    )
    return entry


async def _dhcp(hass, monkeypatch, entry, ip, mac, answering):
    from custom_components.xtool import config_flow
    from custom_components.xtool.const import DOMAIN
    from custom_components.xtool.protocols.base import ConnectionInfo

    async def validate(host):
        if host in answering:
            return ConnectionInfo(host=host, serial_number=SERIAL)
        return "cannot_connect"

    validate_mock = AsyncMock(side_effect=validate)
    monkeypatch.setattr(config_flow, "validate_connection", validate_mock)
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", MagicMock())
    flow = config_flow.XtoolConfigFlow()
    flow.hass = hass
    flow.context = {"source": "dhcp"}
    # As the flow manager sets it
    flow.handler = DOMAIN
    info = SimpleNamespace(ip=ip, macaddress=mac.replace(":", ""), hostname="")
    try:
        result = await flow.async_step_dhcp(info)
    except AbortFlow as err:
        # The flow manager turns this into an abort result
        result = {"type": FlowResultType.ABORT, "reason": err.reason}
    return result, validate_mock


async def test_follows_a_new_address_while_the_laser_starts(hass, monkeypatch, entry) -> None:
    """The old address does not answer and the laser does not answer yet."""
    result, validate = await _dhcp(hass, monkeypatch, entry, "192.0.2.46", ETHERNET_MAC, set())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == "192.0.2.46"
    # The new address was not asked: the MAC says which laser it is
    assert all(call.args[0] != "192.0.2.46" for call in validate.await_args_list)


async def test_keeps_an_address_that_answers(hass, monkeypatch, entry) -> None:
    hass.config_entries.async_update_entry(entry, data={"host": "192.0.2.46"})
    await _dhcp(hass, monkeypatch, entry, "192.0.2.47", WIFI_MAC, {"192.0.2.46", "192.0.2.47"})
    assert entry.data["host"] == "192.0.2.46"


async def test_moves_to_the_preferred_address(hass, monkeypatch, entry) -> None:
    """On Wi-Fi, a P2 moves to its Ethernet address when it asks for it."""
    hass.config_entries.async_update_entry(entry, data={"host": "192.0.2.47"})
    entry.runtime_data = SimpleNamespace(prefers_host=lambda host: host == "192.0.2.46")
    await _dhcp(hass, monkeypatch, entry, "192.0.2.46", ETHERNET_MAC, {"192.0.2.46", "192.0.2.47"})
    assert entry.data["host"] == "192.0.2.46"


@pytest.mark.skipif(has("address_by_discovery"), reason="replaced by the discovery search (test_address_discovery)")
async def test_a_running_p2_takes_the_address_itself(hass, monkeypatch, entry) -> None:
    """Its update listener does not reload for a new address: the coordinator moves."""
    move = AsyncMock()
    entry.runtime_data = SimpleNamespace(prefers_host=lambda host: False, async_move_to=move)
    result, _ = await _dhcp(hass, monkeypatch, entry, "192.0.2.46", ETHERNET_MAC, set())
    assert result["reason"] == "already_configured"
    move.assert_awaited_once_with("192.0.2.46")


async def test_an_unknown_mac_is_a_new_laser(hass, monkeypatch, entry) -> None:
    result, validate = await _dhcp(hass, monkeypatch, entry, "192.0.2.80", "aa:bb:cc:dd:ee:ff", set())
    assert result["type"] is FlowResultType.ABORT
    validate.assert_awaited_once_with("192.0.2.80")
    assert entry.data["host"] == "192.0.2.244"


async def test_p2_prefers_its_ethernet_address(hass, config_entry) -> None:
    from custom_components.xtool.protocols.rest import XTOOL_P2

    from .conftest import build_coordinator

    coordinator = build_coordinator(hass, XTOOL_P2)
    coordinator.protocol.ethernet_address = MagicMock(return_value="192.0.2.46")
    coordinator.protocol.host = "192.0.2.47"
    assert coordinator.prefers_host("192.0.2.46") is True
    assert coordinator.prefers_host("192.0.2.48") is False
    coordinator.protocol.host = "192.0.2.46"
    assert coordinator.prefers_host("192.0.2.46") is False


@pytest.mark.skipif(has("address_by_discovery"), reason="replaced by the discovery search (test_address_discovery)")
async def test_old_addresses_are_not_kept(hass, config_entry) -> None:
    """A pool address from an earlier start is dropped from the saved list."""
    from custom_components.xtool.protocols.rest import XTOOL_P2

    from .conftest import build_coordinator

    coordinator = build_coordinator(hass, XTOOL_P2)
    coordinator.config_entry = config_entry
    coordinator._known_hosts = ["192.0.2.47", "192.0.2.244"]
    store = MagicMock(async_save=AsyncMock())
    coordinator._hosts_store = store
    await coordinator._save_hosts(["192.0.2.46", "192.0.2.47", "192.0.2.46"])
    assert coordinator._known_hosts == ["192.0.2.46", "192.0.2.47"]
    store.async_save.assert_awaited_once_with({"hosts": ["192.0.2.46", "192.0.2.47"]})
