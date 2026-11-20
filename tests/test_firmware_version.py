"""Tests for the laser firmware version (issue #4).

The cloud names the F2 Ultra UV firmware 40.130.023.05; the device
reports the same firmware as 40.130.023.00.ht5.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import requires

pytestmark = requires("firmware_version_normalized")

INSTALLED = "40.130.023.00.ht5"


def _f2_ultra_uv():
    from custom_components.xtool.protocols import DEVICE_MODELS

    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )


def _update_entity(hass: HomeAssistant, cloud_version: str):
    from custom_components.xtool.protocols.base import FirmwareUpdateInfo
    from custom_components.xtool.update import XtoolFirmwareUpdate

    coordinator = build_coordinator(hass, _f2_ultra_uv())
    coordinator.firmware_version = INSTALLED
    entity = XtoolFirmwareUpdate(coordinator)
    entity._update_info = FirmwareUpdateInfo(latest_version=cloud_version, release_summary="notes")
    return entity


def test_same_firmware_written_differently_is_not_an_update(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    entity = _update_entity(hass, "40.130.023.05")
    assert entity.installed_version == INSTALLED
    assert entity.latest_version == INSTALLED
    assert entity.release_notes() == "notes"


@pytest.mark.parametrize("cloud_version", ["40.130.024.01", "40.131.001.00"])
def test_newer_firmware_is_still_an_update(
    hass: HomeAssistant, config_entry: MockConfigEntry, cloud_version: str
) -> None:
    entity = _update_entity(hass, cloud_version)
    assert entity.latest_version == cloud_version


def test_device_page_follows_the_firmware(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """A version read from the device replaces the one stored at setup."""
    coordinator = build_coordinator(hass, _f2_ultra_uv())
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("xtool", "MXTEST0001")},
        sw_version="40.130.022.00.ht1",
    )
    coordinator.laser_device_id = device.id
    coordinator.firmware_version = INSTALLED
    assert registry.async_get(device.id).sw_version == INSTALLED


def _identity(firmware: str = "", serial: str = ""):
    from types import SimpleNamespace

    return SimpleNamespace(
        serial_number=serial,
        laser_power_watts=0,
        laser=None,
        main_firmware=firmware,
        mac_address="",
        hardware_version="",
        sku="",
        machine_style="",
    )


def _polling_coordinator(hass: HomeAssistant):
    from unittest.mock import AsyncMock

    coordinator = build_coordinator(hass, _f2_ultra_uv())
    coordinator.firmware_version = "40.130.022.00.ht1"
    coordinator.protocol.connected = True
    coordinator.protocol.poll_state = AsyncMock()
    coordinator.protocol.disconnect = AsyncMock()
    coordinator._poll_accessories = AsyncMock()
    return coordinator


@requires("device_info_refetch")
async def test_empty_machine_info_is_read_again(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """An empty machineInfo right after power-on does not end the retry."""
    from unittest.mock import AsyncMock

    coordinator = _polling_coordinator(hass)
    coordinator.protocol.get_device_info = AsyncMock(return_value=_identity())
    await coordinator._async_update_data()
    assert coordinator._device_info_fetched is False

    coordinator.protocol.get_device_info = AsyncMock(return_value=_identity(INSTALLED))
    await coordinator._async_update_data()
    assert coordinator._device_info_fetched is True
    assert coordinator.firmware_version == INSTALLED


@requires("device_info_refetch")
async def test_identity_is_read_again_after_reconnect(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """A failed poll (laser off) makes the next connect read the identity again."""
    from unittest.mock import AsyncMock

    coordinator = _polling_coordinator(hass)
    coordinator.protocol.get_device_info = AsyncMock(return_value=_identity(INSTALLED))
    await coordinator._async_update_data()
    assert coordinator._device_info_fetched is True

    coordinator.protocol.poll_state = AsyncMock(side_effect=ConnectionError("laser off"))
    await coordinator._async_update_data()
    assert coordinator._device_info_fetched is False



@requires("firmware_from_device_page")
def test_restart_keeps_the_firmware_from_the_device_page(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Setup starts from the device page's version, not the one stored at setup."""
    from custom_components.xtool import _last_firmware_version

    stored = "40.130.022.00.ht1"
    assert _last_firmware_version(hass, config_entry.entry_id, "MXTEST0001", stored) == stored
    dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("xtool", "MXTEST0001")},
        sw_version=INSTALLED,
    )
    assert _last_firmware_version(hass, config_entry.entry_id, "MXTEST0001", stored) == INSTALLED


@requires("device_info_refetch")
async def test_identity_is_read_again_on_a_new_connection(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """A quick power cycle reconnects without a failed poll; the identity is still read again."""
    from unittest.mock import AsyncMock

    coordinator = _polling_coordinator(hass)
    coordinator.protocol.get_device_info = AsyncMock(return_value=_identity(INSTALLED))
    await coordinator._async_update_data()
    assert coordinator.protocol.get_device_info.await_count == 1

    coordinator.protocol.connected = False
    coordinator.protocol.connect = AsyncMock()
    await coordinator._async_update_data()
    assert coordinator.protocol.get_device_info.await_count == 2
