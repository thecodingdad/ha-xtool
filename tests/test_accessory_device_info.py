"""Accessory parent-link regression tests; run with Home Assistant installed.

python -m unittest discover -s tests
"""

from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

# Load the entity modules without running integration setup. The protocol
# package eagerly imports every model/coordinator and otherwise forms an
# import cycle outside Home Assistant's integration loader.
_ROOT = Path.cwd() / "custom_components" / "xtool"
for _name, _path in (
    ("custom_components.xtool", _ROOT),
    ("custom_components.xtool.protocols", _ROOT / "protocols"),
):
    if _name not in sys.modules:
        _package = ModuleType(_name)
        _package.__path__ = [str(_path)]
        sys.modules[_name] = _package

from custom_components.xtool.protocols.accessories import entities
from custom_components.xtool.protocols.accessories.fire_extinguisher import (
    FIRE_EXTINGUISHER_V1_5,
)
from custom_components.xtool.protocols.base import AccessoryState


class AccessoryDeviceInfoTests(unittest.TestCase):
    """The firmware-only extinguisher must link without deprecated HA APIs."""

    def setUp(self):
        self.coordinator = SimpleNamespace(
            serial_number="laser-serial",
            laser_device_id="laser-registry-id",
            model=SimpleNamespace(model_id="S1"),
            enable_firmware_updates=False,
            data=SimpleNamespace(connected_accessories={
                "FireExtinguisherV1_5:slot4": AccessoryState(
                    type_id="FireExtinguisherV1_5", sn="slot4",
                    fields={"version": "V40.203.002.3224.01B01"},
                ),
            }),
        )

    def test_firmware_only_accessory_uses_registry_parent_id(self):
        with patch.object(entities.DeviceInfo, "__annotations__", {"via_device_id": str}):
            accessory = self.coordinator.data.connected_accessories[
                "FireExtinguisherV1_5:slot4"
            ]
            built = entities.build_accessory_entities(self.coordinator, accessory)
            self.assertEqual(len(built), 1)
            entity = built[0]
            self.assertIsInstance(entity, entities._AccessoryUpdate)
            self.assertEqual(entity.device_info["via_device_id"], "laser-registry-id")
            self.assertNotIn("via_device", entity.device_info)
            self.assertEqual(entity.installed_version, "V40.203.002.3224.01B01")
            self.assertEqual(entity.device_info["identifiers"], {
                ("xtool", "laser-serial:FireExtinguisherV1_5:slot4"),
            })
            self.assertIsNone(entity.device_info["serial_number"])

    def test_regular_accessory_entities_use_same_parent(self):
        with patch.object(entities.DeviceInfo, "__annotations__", {"via_device_id": str}):
            entity = entities._AccessorySensor(
                self.coordinator, FIRE_EXTINGUISHER_V1_5, "slot4",
                FIRE_EXTINGUISHER_V1_5.entities[0],
            )
            self.assertEqual(entity.device_info["via_device_id"], "laser-registry-id")
            self.assertNotIn("via_device", entity.device_info)

    def test_older_home_assistant_keeps_legacy_parent_link(self):
        with patch.object(entities.DeviceInfo, "__annotations__", {"via_device": tuple}):
            entity = entities._AccessoryUpdate(
                self.coordinator, FIRE_EXTINGUISHER_V1_5, "slot4",
            )
            self.assertEqual(entity.device_info["via_device"], ("xtool", "laser-serial"))
            self.assertNotIn("via_device_id", entity.device_info)


if __name__ == "__main__":
    unittest.main()
