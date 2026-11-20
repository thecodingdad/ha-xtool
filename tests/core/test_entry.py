"""A config entry's life: set up, laser not reachable, unload.

The same for every model; the S1 (captured, see
tests/models/s1_capture.py) stands in for a real device.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.const import DOMAIN
from custom_components.xtool.protocols import DEVICE_MODELS

from ..models.s1_capture import SERIAL, FakeS1

PREFIX = f"xtool_s1_{SERIAL.lower()}"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SERIAL, title="xTool S1",
        data={"host": "192.0.2.60", "serial_number": SERIAL, "device_name": "xTool S1",
              "firmware_version": "V40.32.015.2025.01 B10", "laser_power_watts": 2,
              "model_key": "S1_V1", "pairing_token": ""},
    )
    entry.add_to_hass(hass)
    return entry


async def test_set_up_and_unload(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    with patch.object(DEVICE_MODELS["S1_V1"], "protocol_class", FakeS1):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        protocol = entry.runtime_data.protocol
        assert protocol.connected
        assert hass.states.get(f"sensor.{PREFIX}_status").state == "idle"

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not protocol.connected
    assert hass.states.get(f"sensor.{PREFIX}_status").state == "unavailable"


async def test_a_laser_that_is_off_at_setup(hass: HomeAssistant) -> None:
    """The entry loads; without a Power switch, Status is Off and readings unavailable."""
    entry = _entry(hass)

    class Unreachable(FakeS1):
        async def connect(self) -> None:
            raise ConnectionError("no route to host")

    with patch.object(DEVICE_MODELS["S1_V1"], "protocol_class", Unreachable):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert hass.states.get(f"sensor.{PREFIX}_status").state == "off"
        assert hass.states.get(f"sensor.{PREFIX}_laser_position_y").state == "unavailable"
        assert hass.states.get(f"switch.{PREFIX}_buzzer").state == "unavailable"
        await hass.config_entries.async_unload(entry.entry_id)


async def test_the_laser_comes_back(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    reachable = {"up": False}

    class Flaky(FakeS1):
        async def connect(self) -> None:
            if not reachable["up"]:
                raise ConnectionError("no route to host")
            await super().connect()

    with patch.object(DEVICE_MODELS["S1_V1"], "protocol_class", Flaky):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(f"sensor.{PREFIX}_status").state == "off"
        reachable["up"] = True
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(f"sensor.{PREFIX}_status").state == "idle"
        assert hass.states.get(f"sensor.{PREFIX}_laser_position_y").state == "99.84"
        await hass.config_entries.async_unload(entry.entry_id)
