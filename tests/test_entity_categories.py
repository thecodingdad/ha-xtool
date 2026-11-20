"""Every model groups its firmware update and job sensors the same way."""

from __future__ import annotations

import json

from homeassistant.const import EntityCategory

from .features import COMPONENT, requires

pytestmark = requires("entity_categories")


def test_firmware_updates_are_diagnostic() -> None:
    import inspect

    from custom_components.xtool.protocols.accessories.entities import _AccessoryUpdate
    from custom_components.xtool.update import XtoolFirmwareUpdate

    # Home Assistant wraps _attr_ defaults in properties; read the class body
    for cls in (XtoolFirmwareUpdate, _AccessoryUpdate):
        assert "_attr_entity_category = EntityCategory.DIAGNOSTIC" in inspect.getsource(cls)


def test_task_id_is_diagnostic_on_every_protocol() -> None:
    from custom_components.xtool.protocols.rest.entities import SENSOR_DESCRIPTIONS as REST
    from custom_components.xtool.protocols.s1.entities import SENSOR_DESCRIPTIONS as S1
    from custom_components.xtool.protocols.ws_v2.entities import WSV2_SENSOR_DESCRIPTIONS as WSV2

    for descriptions in (REST, S1, WSV2):
        by_key = {d.key: d for d in descriptions}
        assert by_key["task_id"].entity_category is EntityCategory.DIAGNOSTIC
    s1 = {d.key: d for d in S1}
    assert s1["session_count"].entity_category is EntityCategory.DIAGNOSTIC
    assert s1["tool_runtime"].entity_category is EntityCategory.DIAGNOSTIC


def test_exhaust_fan_switch_shows_a_plain_fan() -> None:
    icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))
    assert icons["entity"]["switch"]["smoking_fan"]["default"] == "mdi:fan"
