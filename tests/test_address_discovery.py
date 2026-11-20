"""A laser that stops answering is found again at a new address.

The P2 sometimes starts with a temporary MAC address, so Home Assistant's
DHCP discovery does not know it. It answers xTool's own discovery at any
address, as Studio finds it. The coordinator looks for it while it does not
answer and hands the address to the config flow, which updates the entry and
reloads it, as Home Assistant's discovery does.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import POWER_SWITCH, SERIAL, build_coordinator
from .features import requires

pytestmark = requires("address_by_discovery")

OLD = "192.0.2.46"
NEW = "192.0.2.244"
WIFI = "192.0.2.47"


# --- config flow ------------------------------------------------------------


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    from custom_components.xtool.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"host": OLD})
    entry.add_to_hass(hass)
    return entry


async def _found(hass, monkeypatch, host, answering, serial=SERIAL):
    from custom_components.xtool import config_flow
    from custom_components.xtool.const import DOMAIN
    from custom_components.xtool.protocols.base import ConnectionInfo

    async def validate(address):
        if address in answering:
            return ConnectionInfo(host=address, serial_number=SERIAL)
        return "cannot_connect"

    monkeypatch.setattr(config_flow, "validate_connection", AsyncMock(side_effect=validate))
    reload = MagicMock()
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload)
    flow = config_flow.XtoolConfigFlow()
    flow.hass = hass
    flow.context = {"source": "integration_discovery"}
    flow.handler = DOMAIN
    try:
        result = await flow.async_step_integration_discovery({"host": host, "serial_number": serial})
    except AbortFlow as err:
        result = {"type": FlowResultType.ABORT, "reason": err.reason}
    return result, reload


async def test_moves_when_the_address_in_use_does_not_answer(hass, monkeypatch, entry) -> None:
    from homeassistant.config_entries import ConfigEntryState

    entry.mock_state(hass, ConfigEntryState.LOADED)
    result, reload = await _found(hass, monkeypatch, NEW, {NEW})
    assert result["type"] is FlowResultType.ABORT
    assert entry.data["host"] == NEW
    # Not reloaded by the flow: the update listener moves the running laser
    reload.assert_not_called()


async def test_keeps_an_address_that_answers(hass, monkeypatch, entry) -> None:
    """A laser on Ethernet and Wi-Fi answers at both; the one in use is kept."""
    await _found(hass, monkeypatch, WIFI, {OLD, WIFI})
    assert entry.data["host"] == OLD


async def test_moves_to_the_preferred_address(hass, monkeypatch, entry) -> None:
    """A P2 on Wi-Fi moves to its Ethernet address when it answers there."""
    hass.config_entries.async_update_entry(entry, data={"host": WIFI})
    entry.runtime_data = SimpleNamespace(prefers_host=lambda host: host == OLD)
    await _found(hass, monkeypatch, OLD, {OLD, WIFI})
    assert entry.data["host"] == OLD


async def test_a_laser_not_set_up_is_left_alone(hass, monkeypatch, entry) -> None:
    result, reload = await _found(hass, monkeypatch, NEW, {NEW}, serial="MXTEST9999")
    assert result["type"] is FlowResultType.ABORT
    assert entry.data["host"] == OLD
    reload.assert_not_called()


async def test_a_new_address_is_used_without_a_reload(hass, entry) -> None:
    """The update listener moves the running laser to the saved address."""
    from custom_components.xtool import _async_options_updated

    reload = AsyncMock()
    hass.config_entries.async_reload = reload
    coordinator = SimpleNamespace(
        protocol=SimpleNamespace(host=OLD),
        async_use_host=AsyncMock(return_value=True),
        options_at_setup=dict(entry.options), options_snapshot=dict(entry.options),
    )
    entry.runtime_data = coordinator
    hass.config_entries.async_update_entry(entry, data={"host": NEW})
    await _async_options_updated(hass, entry)
    coordinator.async_use_host.assert_awaited_once_with(NEW)
    reload.assert_not_awaited()
    # A protocol that cannot change its address while it runs is reloaded
    coordinator.async_use_host = AsyncMock(return_value=False)
    await _async_options_updated(hass, entry)
    reload.assert_awaited_once()
    # An options change still reloads
    reload.reset_mock()
    coordinator.protocol.host = NEW
    hass.config_entries.async_update_entry(entry, options={"x": 1})
    await _async_options_updated(hass, entry)
    reload.assert_awaited_once()


async def test_the_p2_changes_its_address_in_place(hass, coordinator) -> None:
    coordinator.protocol.change_host = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator._unreachable_since = 5.0
    assert await coordinator.async_use_host(NEW) is True
    coordinator.protocol.change_host.assert_awaited_once_with(NEW)
    assert coordinator.host == NEW
    assert coordinator._unreachable_since is None
    coordinator.async_request_refresh.assert_awaited_once()


# --- coordinator ------------------------------------------------------------


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.protocols.rest import XTOOL_P2

    coord = build_coordinator(hass, XTOOL_P2)
    coord.config_entry = config_entry
    coord.protocol.host = OLD
    hass.states.async_set(POWER_SWITCH, "on")

    def background(hass, coro, name):
        coro.close()
        return MagicMock(done=lambda: True)

    config_entry.async_create_background_task = MagicMock(side_effect=background)
    return coord


def _clock(monkeypatch, start=1000.0):
    from custom_components.xtool import coordinator as base

    now = [start]
    monkeypatch.setattr(base.time, "monotonic", lambda: now[0])
    return now


def test_searches_often_at_first_then_rarely(hass, coordinator, monkeypatch) -> None:
    now = _clock(monkeypatch)
    started = coordinator.config_entry.async_create_background_task

    def searches_in(seconds, step=1.0):
        before = started.call_count
        end = now[0] + seconds
        while now[0] < end:
            coordinator.async_search_address(False)
            now[0] += step
        return started.call_count - before

    # Every 5 s during the first 3 min after it stopped answering
    assert searches_in(180) == 36
    # Every 30 s up to 10 min
    assert searches_in(420) == 14
    # Then every 15 min
    assert searches_in(1800, step=10.0) == 2


def test_no_search_while_it_answers_or_its_power_is_off(hass, coordinator, monkeypatch) -> None:
    _clock(monkeypatch)
    started = coordinator.config_entry.async_create_background_task
    coordinator.async_search_address(True)
    started.assert_not_called()
    hass.states.async_set(POWER_SWITCH, "off")
    coordinator.async_search_address(False)
    started.assert_not_called()
    hass.states.async_set(POWER_SWITCH, "on")
    coordinator.async_search_address(False)
    started.assert_called_once()


def test_answering_again_starts_the_fast_searches_over(hass, coordinator, monkeypatch) -> None:
    now = _clock(monkeypatch)
    coordinator.async_search_address(False)
    now[0] += 1000
    coordinator.async_search_address(True)
    assert coordinator._unreachable_since is None
    coordinator.async_search_address(False)
    assert coordinator._next_address_search == now[0] + 5.0


async def test_the_search_hands_the_laser_to_the_config_flow(hass, coordinator, monkeypatch) -> None:
    """Only a discovered P2 that answers with this serial number is taken."""
    from custom_components.xtool import coordinator as base
    from custom_components.xtool import discovery, protocols
    from custom_components.xtool.discovery import DiscoveredDevice
    from custom_components.xtool.protocols.base import ConnectionInfo

    devices = [
        DiscoveredDevice(ip=OLD, name="P2", version=""),           # the address in use
        DiscoveredDevice(ip="192.0.2.60", name="F1", version=""),  # another model
        DiscoveredDevice(ip="192.0.2.61", name="P2", version=""),  # another P2
        DiscoveredDevice(ip=NEW, name="P2", version=""),
    ]
    monkeypatch.setattr(discovery, "discover_v1", AsyncMock(return_value=devices))
    serials = {"192.0.2.61": "MXTEST9999", NEW: SERIAL}

    async def validate(host):
        return ConnectionInfo(host=host, serial_number=serials[host])

    validate_mock = AsyncMock(side_effect=validate)
    monkeypatch.setattr(protocols, "validate_connection", validate_mock)
    create_flow = MagicMock()
    monkeypatch.setattr(base.discovery_flow, "async_create_flow", create_flow)

    await coordinator._async_search_address()

    assert [call.args[0] for call in validate_mock.await_args_list] == ["192.0.2.61", NEW]
    create_flow.assert_called_once()
    assert create_flow.call_args.kwargs["context"] == {"source": "integration_discovery"}
    assert create_flow.call_args.kwargs["data"] == {"host": NEW, "serial_number": SERIAL}


async def test_ethernet_is_handed_to_the_config_flow(hass, coordinator, monkeypatch) -> None:
    """On Wi-Fi with a cable connected, the Ethernet address goes to the flow."""
    from custom_components.xtool import coordinator as base
    from custom_components.xtool.protocols.rest import coordinator as rest_coordinator

    coordinator.protocol.host = WIFI
    coordinator.protocol.ethernet_address = MagicMock(return_value=OLD)
    create_flow = MagicMock()
    monkeypatch.setattr(base.discovery_flow, "async_create_flow", create_flow)
    monkeypatch.setattr(rest_coordinator, "_serial_at", AsyncMock(return_value="MXTEST9999"))
    await coordinator._prefer_ethernet()
    create_flow.assert_not_called()
    monkeypatch.setattr(rest_coordinator, "_serial_at", AsyncMock(return_value=SERIAL))
    await coordinator._prefer_ethernet()
    assert create_flow.call_args.kwargs["data"] == {"host": OLD, "serial_number": SERIAL}
