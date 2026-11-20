"""The pairing steps of the reauth and reconfigure flows."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.const import DOMAIN
from custom_components.xtool.protocols import ConnectionInfo

SERIAL = "MXTEST0001"
HOST = "192.0.2.10"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        data={"host": HOST, "model_key": "F2UltraUV_V2", "serial_number": SERIAL, "pairing_token": "old"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture(autouse=True)
async def no_device_setup(hass: HomeAssistant) -> AsyncIterator[None]:
    """Load and unload the entry without talking to a laser."""
    with (
        patch("custom_components.xtool.async_setup_entry", AsyncMock(return_value=True)),
        patch("custom_components.xtool.async_unload_entry", AsyncMock(return_value=True)),
    ):
        yield
        # Unload here, while setup and unload are still the stand-ins
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


def _pairing(results: list[ConnectionInfo | str]):
    """Stand in for the pairing; it answers at once, before any await."""
    calls: list[str] = []

    async def pair(*args, **kwargs):
        calls.append(args[0])
        return results.pop(0)

    return patch("custom_components.xtool.config_flow.validate_with_model", pair), calls


async def _run_pairing(hass: HomeAssistant, flow_id: str) -> dict:
    """Submit the pairing step and let the pairing task finish."""
    result = await hass.config_entries.flow.async_configure(flow_id, user_input={})
    while result["type"] is FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(flow_id)
    return result


async def test_a_pairing_that_fails_at_once_shows_the_error(hass: HomeAssistant, entry) -> None:
    """It used to start again with the same input, in a loop that blocked Home Assistant."""
    pairing, calls = _pairing(["unknown_model"])
    with pairing:
        result = await entry.start_reauth_flow(hass)
        result = await _run_pairing(hass, result["flow_id"])
    assert calls == [HOST]
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair_confirm"
    assert result["errors"] == {"base": "unknown_model"}


async def test_a_new_submit_pairs_again(hass: HomeAssistant, entry) -> None:
    pairing, calls = _pairing(["pairing_timeout", ConnectionInfo(host=HOST, serial_number=SERIAL, pairing_token="new")])
    with pairing:
        result = await entry.start_reauth_flow(hass)
        result = await _run_pairing(hass, result["flow_id"])
        assert result["errors"] == {"base": "pairing_timeout"}
        result = await _run_pairing(hass, result["flow_id"])
    await hass.async_block_till_done()
    assert calls == [HOST, HOST]
    assert result["reason"] == "reauth_successful"
    assert entry.data["pairing_token"] == "new"
