"""Tests for state-based icons on switches and binary sensors (issue #13)."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass

from .conftest import Built
from .features import COMPONENT, requires

PLATFORMS = ("switch", "binary_sensor")


def _icons() -> dict[str, Any]:
    return json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))[
        "entity"
    ]


def test_every_model_builds_its_entities(built_entities: list[Built]) -> None:
    """Switches and binary sensors build for every model and accessory."""
    platforms = {item.platform for item in built_entities}
    assert platforms == set(PLATFORMS)


@requires("icon_translations")
def test_no_fixed_icons(built_entities: list[Built]) -> None:
    """A fixed icon would hide the state icons from icons.json."""
    fixed = [f"{item} ({item.entity.icon})" for item in built_entities if item.entity.icon]
    assert not fixed, "\n".join(sorted(set(fixed)))


@requires("icon_translations")
def test_icons_json_has_no_unused_entries(built_entities: list[Built]) -> None:
    """Every icons.json entry belongs to an entity the integration creates."""
    icons = _icons()
    for platform in PLATFORMS:
        used = {
            item.entity.translation_key
            for item in built_entities
            if item.platform == platform
        }
        unused = set(icons.get(platform, {})) - used
        assert not unused, f"{platform}: {sorted(unused)}"


@requires("icon_translations")
def test_icons_json_entries_are_well_formed() -> None:
    """Each entry has a default icon; switches and binary sensors have only on/off state icons."""
    for platform, entries in _icons().items():
        for key, entry in entries.items():
            assert entry["default"].startswith("mdi:"), f"{platform}.{key}"
            states = entry.get("state", {})
            if platform in PLATFORMS:
                assert set(states) <= {"on", "off"}, f"{platform}.{key}"
            assert all(icon.startswith("mdi:") for icon in states.values())


@requires("icon_translations")
def test_power_switch_uses_outlet_icons(built_entities: list[Built]) -> None:
    """The power switch shows the outlet's plug and plug-off icons."""
    power = [
        item.entity
        for item in built_entities
        if item.entity.translation_key == "power_switch"
    ]
    assert power
    for entity in power:
        assert entity.device_class == SwitchDeviceClass.OUTLET
        assert entity.icon is None
    assert "power_switch" not in _icons().get("switch", {})
