"""Shared fixtures: build the entities of every supported model and accessory.

No laser is needed. Each model's real coordinator class is built around a
mock protocol, and its entity builders run as they do at setup, before the
first poll.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import current_entry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Without the import-order fix, the first import of the package fails on a
# circular import and only a second attempt succeeds. Home Assistant retries
# the import in the same way, so the tests do too.
try:
    import custom_components.xtool  # noqa: F401
except ImportError:
    import custom_components.xtool  # noqa: F401

from custom_components.xtool.const import DOMAIN
from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.accessories import ACCESSORY_DEFINITIONS
from custom_components.xtool.protocols.accessories.entities import (
    build_accessory_entities,
)
from custom_components.xtool.protocols.base import AccessoryState
from custom_components.xtool.switch import XtoolPowerSwitch

SERIAL = "MXTEST0001"
POWER_SWITCH = "switch.laser_plug"


@dataclass(frozen=True)
class Built:
    """One entity, with where it came from for assertion messages."""

    source: str
    platform: str
    entity: Entity

    def __str__(self) -> str:
        key = self.entity.translation_key
        return f"{self.source} {self.platform}.{key}"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Load integrations from custom_components in every test."""


@pytest.fixture
def config_entry(hass: HomeAssistant) -> Iterator[MockConfigEntry]:
    """A config entry, set as the current one while coordinators are built."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={})
    entry.add_to_hass(hass)
    token = current_entry.set(entry)
    yield entry
    current_entry.reset(token)


def build_coordinator(hass: HomeAssistant, model: Any) -> Any:
    """Build a model's real coordinator around a mock protocol."""
    protocol = MagicMock()
    protocol.host = "192.0.2.10"
    return model.coordinator_class(
        hass,
        protocol,
        "xTool Test",
        SERIAL,
        "1.0.0",
        model,
        power_switch_entity_id=POWER_SWITCH,
    )


@pytest.fixture
def built_entities(hass: HomeAssistant, config_entry: MockConfigEntry) -> list[Built]:
    """Switches and binary sensors of every model and accessory."""
    built: list[Built] = []
    coordinator = None
    for key, model in DEVICE_MODELS.items():
        coordinator = build_coordinator(hass, model)
        built += [Built(key, "switch", e) for e in coordinator.build_switches()]
        built += [
            Built(key, "binary_sensor", e)
            for e in coordinator.build_binary_sensors()
        ]
        built.append(Built(key, "switch", XtoolPowerSwitch(coordinator)))
    assert coordinator is not None
    for type_id, definition in ACCESSORY_DEFINITIONS.items():
        fields = {spec.field: 0 for spec in definition.entities if spec.field}
        accessory = AccessoryState(type_id=type_id, sn="38:36:0C:01:C1:96", fields=fields)
        for entity in build_accessory_entities(coordinator, accessory):
            platform = getattr(entity, "_xtool_platform", None)
            if platform in ("switch", "binary_sensor"):
                built.append(Built(f"accessory {type_id}", platform, entity))
    return built


@pytest.fixture(autouse=True)
def no_address_search_on_the_network(monkeypatch):
    """A failed poll starts the discovery search for a new address; tests
    must not send discovery on the real network."""
    from unittest.mock import AsyncMock

    from custom_components.xtool import discovery

    if hasattr(discovery, "discover_v1"):
        monkeypatch.setattr(discovery, "discover_v1", AsyncMock(return_value=[]))


@pytest.fixture
async def v2_device():
    """A fake WS-V2 device (see fake_ws_v2.py) in place of the network."""
    from unittest.mock import patch

    from custom_components.xtool.protocols.ws_v2 import protocol as ws_v2

    from .fake_ws_v2 import Device

    device = Device()
    with patch.object(ws_v2.aiohttp, "ClientSession", side_effect=device.session):
        yield device


@pytest.fixture
async def v2_protocol(v2_device):
    """A WS-V2 protocol, with a pairing token, talking to ``v2_device``."""
    from custom_components.xtool.protocols.ws_v2 import protocol as ws_v2

    from .fake_ws_v2 import PAIRING_TOKEN

    protocol = ws_v2.WSV2Protocol("printer.test")
    protocol.pairing_token = PAIRING_TOKEN
    yield protocol
    await protocol.disconnect()
