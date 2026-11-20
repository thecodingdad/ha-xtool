"""An F1 Ultra that names itself "xTool_F1_Ultra" (issue #27).

The underscores hid "F1Ultra" from the model detection, which found only
"F1": the entry was set up as an F1, without camera, fill light and gyro.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.const import DOMAIN

from .fake_ws_v2 import PAIRING_TOKEN

SERIAL = "MXTEST0001"


async def _set_up(hass: HomeAssistant, name: str, model_key: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SERIAL, title=name,
        data={"host": "192.0.2.10", "serial_number": SERIAL, "device_name": name,
              "model_key": model_key, "pairing_token": PAIRING_TOKEN},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


def _has_fill_light(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    return any(
        e.unique_id == f"{SERIAL}_fill_light"
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    )


async def test_an_entry_set_up_as_an_f1_becomes_an_f1_ultra(hass: HomeAssistant, v2_device) -> None:
    entry = await _set_up(hass, "xTool_F1_Ultra", "F1_V2")
    assert entry.data["model_key"] == "F1Ultra_V2"
    assert _has_fill_light(hass, entry)
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(("name", "model_key"), [
    ("xTool_F1", "F1_V2"),            # an F1 is an F1
    ("Garage F1 Ultra", "F1_V2"),     # names without underscores are left alone
    ("xTool_F1_Ultra", "GS003_V2"),   # a model the name does not stand for was picked by hand
])
async def test_other_entries_keep_their_model(hass: HomeAssistant, v2_device, name: str, model_key: str) -> None:
    entry = await _set_up(hass, name, model_key)
    assert entry.data["model_key"] == model_key
    await hass.config_entries.async_unload(entry.entry_id)
