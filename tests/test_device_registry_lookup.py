"""Device registry lookups use the helpers Home Assistant keeps.

Home Assistant 2026.11 deprecates ``device_registry.devices`` as a
mapping and ``async_get_device``; both stop working in 2027.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import SERIAL, build_coordinator
from .features import COMPONENT, requires

pytestmark = requires("device_registry_lookup")

DOMAIN = "xtool"


@pytest.mark.parametrize("path", ["coordinator.py", "__init__.py"])
def test_no_deprecated_lookups(path: str) -> None:
    text = (COMPONENT / path).read_text(encoding="utf-8")
    assert "devices.values()" not in text
    assert ".async_get_device(" not in text


async def test_orphan_mac_devices_are_removed(hass: HomeAssistant) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN, data={})
    other.add_to_hass(hass)
    reg = dr.async_get(hass)
    orphan = f"{SERIAL}:aa:bb:cc:dd:ee:ff"
    reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, orphan)})
    reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, SERIAL)})
    # Another entry's device stays, even with the same shape.
    foreign = f"{SERIAL}:11:22:33:44:55:66"
    reg.async_get_or_create(config_entry_id=other.entry_id, identifiers={(DOMAIN, foreign)})
    model = next(m for m in DEVICE_MODELS.values() if m.protocol_version == "V2")
    coordinator = build_coordinator(hass, model)
    coordinator.config_entry = entry
    coordinator.data = type("S", (), {"connected_accessories": {}})()
    coordinator._cleanup_orphan_accessory_devices()
    mine = {i for d in dr.async_entries_for_config_entry(reg, entry.entry_id) for _, i in d.identifiers}
    theirs = {i for d in dr.async_entries_for_config_entry(reg, other.entry_id) for _, i in d.identifiers}
    assert mine == {SERIAL}
    assert theirs == {foreign}
