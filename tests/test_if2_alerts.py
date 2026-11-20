"""The IF2 2.0 Alert event: fan stall and cleaning needed.

The IF2 2.0 reports alerts as bits in the ``S`` token of its M9064 push
(1 = fan stall, 2 = cleaning needed), as Studio reads them.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import COMPONENT, requires

pytestmark = requires("if2_alerts")

IF2_SN = "MXAK013001250917TAF0FC0"


@pytest.fixture
def alert(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.protocols import DEVICE_MODELS
    from custom_components.xtool.protocols.accessories import ACCESSORY_DEFINITIONS
    from custom_components.xtool.protocols.accessories.entities import build_accessory_entities
    from custom_components.xtool.protocols.base import AccessoryState, XtoolDeviceState

    model = next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )
    coordinator = build_coordinator(hass, model)
    fields = ACCESSORY_DEFINITIONS["DuctFanV3"].parse_info('A40.213.004 B4 C2 D2 E:"SN" Z1')
    accessory = AccessoryState(type_id="DuctFanV3", sn=IF2_SN, fields=fields)
    state = XtoolDeviceState()
    state.available = True
    state.connected_accessories = {f"DuctFanV3:{IF2_SN}": accessory}
    coordinator.data = state
    entity = next(
        e for e in build_accessory_entities(coordinator, accessory)
        if getattr(e, "_xtool_platform", None) == "event"
    )
    entity.async_write_ha_state = MagicMock()
    entity._trigger_event = MagicMock()
    return entity, accessory


def _push(accessory, body: str) -> None:
    from custom_components.xtool.protocols.accessories import duct_fan

    accessory.fields.update(duct_fan.parse_fan_v3_push(body))


def test_built_before_any_alert_is_reported(alert) -> None:
    entity, _ = alert
    assert entity.event_types == ["fan_stall", "cleaning_needed"]
    assert entity.translation_key == "accessory_ductfanv3_alert"
    assert entity.unique_id.endswith("_alert")


def test_fires_when_an_alert_turns_on(alert) -> None:
    entity, accessory = alert
    _push(accessory, "A1 B3 C3 D0 S0")
    entity._handle_coordinator_update()  # first value: nothing fires
    _push(accessory, "A1 B3 C3 D0 S1")
    entity._handle_coordinator_update()
    entity._trigger_event.assert_called_once_with("fan_stall")
    entity._trigger_event.reset_mock()
    _push(accessory, "A1 B3 C3 D0 S3")
    entity._handle_coordinator_update()
    entity._trigger_event.assert_called_once_with("cleaning_needed")
    entity._trigger_event.reset_mock()
    # Still on: no repeat. Off, then on again: fires again.
    entity._handle_coordinator_update()
    _push(accessory, "A1 B3 C3 D0 S0")
    entity._handle_coordinator_update()
    _push(accessory, "A1 B3 C3 D0 S2")
    entity._handle_coordinator_update()
    entity._trigger_event.assert_called_once_with("cleaning_needed")


def test_alerts_present_at_start_are_not_fired(alert) -> None:
    entity, accessory = alert
    _push(accessory, "A1 B3 C3 D0 S3")
    entity._handle_coordinator_update()
    entity._trigger_event.assert_not_called()


def test_event_platform_takes_accessory_entities() -> None:
    text = (COMPONENT / "event.py").read_text(encoding="utf-8")
    assert 'register_platform_add("event"' in text
    assert 'initial_accessory_entities("event")' in text


@pytest.mark.parametrize(
    "path", ["strings.json", "translations/en.json", "translations/de.json"],
)
def test_translated(path: str) -> None:
    events = json.loads((COMPONENT / path).read_text(encoding="utf-8"))["entity"]["event"]
    states = events["accessory_ductfanv3_alert"]["state_attributes"]["event_type"]["state"]
    assert set(states) == {"fan_stall", "cleaning_needed"}
