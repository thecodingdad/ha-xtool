"""New entities get the serial-prefixed entity ID, whatever the language.

Home Assistant derives a new entity's ID from its translated name unless
the entity sets ``entity_id`` itself; a German install got IDs such as
``number.xtool_s1_automatisch_ausschalten``.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
from pytest_homeassistant_custom_component.common import MockEntityPlatform

from custom_components.xtool.const import DOMAIN
from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.accessories import ACCESSORY_DEFINITIONS
from custom_components.xtool.protocols.accessories.entities import build_accessory_entities
from custom_components.xtool.protocols.base import AccessoryState

from .conftest import SERIAL, build_coordinator

BUILDERS = (
    "switches", "numbers", "buttons", "sensors", "binary_sensors", "lights",
    "cameras", "images", "fans", "locks", "selects", "updates", "events",
)


def _entities(hass: HomeAssistant) -> list:
    entities, coordinator = [], None
    for model in DEVICE_MODELS.values():
        coordinator = build_coordinator(hass, model)
        for builder in BUILDERS:
            entities += getattr(coordinator, f"build_{builder}")()
    for type_id, definition in ACCESSORY_DEFINITIONS.items():
        fields = {spec.field: 0 for spec in definition.entities if spec.field}
        accessory = AccessoryState(type_id=type_id, sn="38:36:0C:01:C1:96", fields=fields)
        entities += build_accessory_entities(coordinator, accessory)
    return entities


def test_every_entity_has_the_serial_prefixed_id(hass: HomeAssistant, config_entry) -> None:
    entities = _entities(hass)
    assert entities
    for entity in entities:
        domain, object_id = entity.entity_id.split(".", 1)
        assert valid_entity_id(entity.entity_id), entity.entity_id
        assert domain in {
            "binary_sensor", "button", "camera", "event", "fan", "image", "light",
            "lock", "number", "select", "sensor", "switch", "update",
        }, entity.entity_id
        key = entity.unique_id.removeprefix(f"{SERIAL}_")
        assert object_id.startswith("xtool_") and object_id.endswith(slugify(key)), entity.entity_id


async def test_a_new_entity_is_registered_under_it(hass: HomeAssistant, config_entry) -> None:
    from custom_components.xtool.number import XtoolAutoPowerOff

    hass.config.language = "de"
    coordinator = build_coordinator(hass, DEVICE_MODELS["S1_V1"])
    platform = MockEntityPlatform(hass, domain="number", platform_name=DOMAIN)
    await platform.async_add_entities([XtoolAutoPowerOff(coordinator)])
    assert hass.states.get(f"number.xtool_s1_{SERIAL.lower()}_auto_power_off") is not None


async def test_a_registered_entity_keeps_its_id(hass: HomeAssistant, config_entry) -> None:
    from custom_components.xtool.number import XtoolAutoPowerOff

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "number", DOMAIN, f"{SERIAL}_auto_power_off",
        suggested_object_id="xtool_s1_automatisch_ausschalten",
    )
    coordinator = build_coordinator(hass, DEVICE_MODELS["S1_V1"])
    platform = MockEntityPlatform(hass, domain="number", platform_name=DOMAIN)
    await platform.async_add_entities([XtoolAutoPowerOff(coordinator)])
    assert hass.states.get("number.xtool_s1_automatisch_ausschalten") is not None
    assert hass.states.get(f"number.xtool_s1_{SERIAL.lower()}_auto_power_off") is None


@pytest.mark.parametrize("model_key", ["F2UltraUV_V2", "P2_V1"])
def test_ids_are_unique_per_model(hass: HomeAssistant, config_entry, model_key) -> None:
    coordinator = build_coordinator(hass, DEVICE_MODELS[model_key])
    ids = [e.entity_id for b in BUILDERS for e in getattr(coordinator, f"build_{b}")()]
    assert len(ids) == len(set(ids))
