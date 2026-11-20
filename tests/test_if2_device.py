"""Tests for the IF2 2.0 device page and entity names (issue #4)."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import COMPONENT, requires

IF2_SN = "MXAK013001250917TAF0FC0"
INFO = 'A40.213.004.4E22.01.B02 B2 C3 D3 E:"MXAK013001250917TAF0FC0" S0 Z1'


def _if2_entities(hass: HomeAssistant):
    from custom_components.xtool.protocols import DEVICE_MODELS
    from custom_components.xtool.protocols.accessories import ACCESSORY_DEFINITIONS
    from custom_components.xtool.protocols.accessories.entities import build_accessory_entities
    from custom_components.xtool.protocols.base import AccessoryState, XtoolDeviceState

    model = next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )
    coordinator = build_coordinator(hass, model)
    fields = ACCESSORY_DEFINITIONS["DuctFanV3"].parse_info(INFO)
    fields["post_run_seconds"] = 5
    accessory = AccessoryState(type_id="DuctFanV3", sn=IF2_SN, fields=fields)
    state = XtoolDeviceState()
    state.available = True
    state.connected_accessories = {f"DuctFanV3:{IF2_SN}": accessory}
    coordinator.data = state
    return build_accessory_entities(coordinator, accessory)


@requires("accessory_firmware_on_device")
def test_every_if2_entity_has_a_name(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Each IF2 2.0 entity's name is in its own platform's translations."""
    en = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))["entity"]
    missing = [
        f"{entity._xtool_platform}.{entity.translation_key}"
        for entity in _if2_entities(hass)
        if entity.translation_key not in en.get(entity._xtool_platform, {})
    ]
    assert not missing, missing


@requires("accessory_firmware_on_device")
def test_if2_device_page_shows_its_firmware(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """The IF2 device carries the firmware version from its info reply."""
    entities = _if2_entities(hass)
    assert entities
    for entity in entities:
        assert entity.device_info["sw_version"] == "40.213.004.4E22.01.B02", entity.translation_key
