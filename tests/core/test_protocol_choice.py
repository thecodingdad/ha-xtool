"""V1 or V2: decided by the discovery reply, as Studio's discover worker does.

The legacy UDP reply means V1 firmware, the encrypted one V2. Nothing
probes port 28900: an F1 on V1 firmware 40.51.013 was set up as V2 and
failed its handshake (#91).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.const import DOMAIN
from custom_components.xtool.discovery import DiscoveredDevice
from custom_components.xtool.protocols import DEVICE_MODELS, validate_connection
from custom_components.xtool.protocols.base import DeviceInfo

HOST = "192.0.2.91"


class _Laser:
    """Answers whichever protocol the integration picked."""

    picked: list[str] = []

    def __init__(self, host: str) -> None:
        self.host = host

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def get_device_info(self) -> DeviceInfo:
        return DeviceInfo(device_name="xTool F1", serial_number="MXTEST0091F1", main_firmware="40.51.013")

    async def get_version(self) -> str:
        return "40.51.013"


@pytest.mark.parametrize(("answered", "model_key"), [("V1", "F1_V1"), ("V2", "F1_V2")])
async def test_the_discovery_reply_picks_the_protocol(answered: str, model_key: str) -> None:
    found = DiscoveredDevice(ip=HOST, name="xTool F1", version="40.51.013", protocol_version=answered)
    with (
        patch("custom_components.xtool.protocols.identify_host", AsyncMock(return_value=found)),
        patch.object(DEVICE_MODELS["F1_V1"], "protocol_class", _Laser),
        patch.object(DEVICE_MODELS["F1_V2"], "protocol_class", _Laser),
    ):
        info = await validate_connection(HOST)
    assert f"{info.model_id}_{info.protocol_version}" == model_key


@pytest.mark.parametrize(("answered", "model_key"), [(None, "F1_V1"), ("V1", "F1_V1"), ("V2", "F1_V2")])
async def test_a_v1_entry_moves_to_v2_only_on_a_v2_reply(
    hass: HomeAssistant, answered: str | None, model_key: str,
) -> None:
    """A laser updated to V2 firmware after its setup answers the V2 discovery."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="MXTEST0091F1",
        data={"host": HOST, "serial_number": "MXTEST0091F1", "device_name": "xTool F1", "model_key": "F1_V1"},
    )
    entry.add_to_hass(hass)
    found = None if answered is None else DiscoveredDevice(
        ip=HOST, name="xTool F1", version="40.51.020", protocol_version=answered,
    )
    with (
        patch("custom_components.xtool.discovery.identify_host", AsyncMock(return_value=found)),
        patch.object(DEVICE_MODELS["F1_V1"], "protocol_class", _Laser),
        patch.object(DEVICE_MODELS["F1_V2"], "protocol_class", _Laser),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.data["model_key"] == model_key
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
