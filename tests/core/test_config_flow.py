"""Setting up a laser: discovery, manual address, model picker, options.

The flow is the same for every model; the S1 (captured, see
tests/models/s1_capture.py) stands in for a real device.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.const import DOMAIN
from custom_components.xtool.discovery import DiscoveredDevice
from custom_components.xtool.protocols import DEVICE_MODELS

from ..models.s1_capture import SERIAL, FakeS1

HOST = "192.0.2.60"
S1 = DiscoveredDevice(ip=HOST, name="xTool S1", version="40.32.015")


@pytest.fixture
def laser() -> Iterator[dict]:
    """An S1 on the network: discovery finds it, its protocol answers."""
    network = {"discovered": [S1], "identify": S1}
    with (
        patch("custom_components.xtool.config_flow.discover_devices", AsyncMock(side_effect=lambda **_: network["discovered"])),
        patch("custom_components.xtool.protocols.identify_host", AsyncMock(side_effect=lambda _h: network["identify"])),
        patch.object(DEVICE_MODELS["S1_V1"], "protocol_class", FakeS1),
    ):
        yield network


async def _start(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


def _assert_s1_entry(result: dict) -> None:
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "xTool S1"
    assert result["result"].unique_id == SERIAL
    assert result["data"] == {
        "host": HOST,
        "serial_number": SERIAL,
        "device_name": "xTool S1",
        "firmware_version": "V40.32.015.2025.01 B10",
        "laser_power_watts": 2,
        "model_key": "S1_V1",
        "pairing_token": "",
    }


async def test_pick_a_discovered_laser(hass: HomeAssistant, laser) -> None:
    result = await _start(hass)
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device": HOST})
    await hass.async_block_till_done()
    _assert_s1_entry(result)
    assert result["result"].state is ConfigEntryState.LOADED


async def test_enter_the_address_when_nothing_is_found(hass: HomeAssistant, laser) -> None:
    laser["discovered"] = []
    result = await _start(hass)
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": f" {HOST} "})
    await hass.async_block_till_done()
    _assert_s1_entry(result)


async def test_choose_manual_entry_from_the_list(hass: HomeAssistant, laser) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device": "_manual_"})
    assert result["step_id"] == "manual"


async def test_a_laser_set_up_already_is_not_added_twice(hass: HomeAssistant, laser) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"host": HOST}).add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device": HOST})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize("identify", ["no reply", "unknown name"])
async def test_an_unidentified_laser_goes_to_the_model_picker(hass: HomeAssistant, laser, identify) -> None:
    laser["discovered"] = []
    laser["identify"] = None if identify == "no reply" else DiscoveredDevice(ip=HOST, name="Laser 3000", version="1")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
    assert result["step_id"] == "pick_model"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST, "model": "S1_V1"})
    await hass.async_block_till_done()
    _assert_s1_entry(result)


async def test_a_laser_that_does_not_answer_shows_an_error(hass: HomeAssistant, laser) -> None:
    laser["discovered"] = []
    result = await _start(hass)
    with patch.object(FakeS1, "connect", AsyncMock(side_effect=ConnectionError("refused"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "protocol_failed"}


async def test_changed_options_reload_the_entry(hass: HomeAssistant, laser) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device": HOST})
    await hass.async_block_till_done()
    entry = result["result"]
    first = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"scan_interval": 10})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["scan_interval"] == 10
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not first
    assert entry.runtime_data.update_interval.total_seconds() == 10
