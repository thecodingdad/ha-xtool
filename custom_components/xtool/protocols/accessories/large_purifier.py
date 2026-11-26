"""xTool SafetyPro AP2 and AP2 Max — five-filter purifiers.

Same M9033 wire shape and parser as ``Purifier``. As Studio's accessory
runtime has them:

- ``LargePurifier`` (``0x45``) is the AP2: prefix ``[69,115,96,1,0]``,
  firmware ``xTool-bigPurifier-firmware``. Lasers that list their
  accessories with M9098 report it as ``45…``; the X1, which lists them
  itself, has it as ``Purifier`` (platform id 69).
- ``LargePurifierV3`` (``0x4C``) is the AP2 Max: prefix
  ``[76,115,107,1,0]``, firmware ``xTool-BigPurifier2.0-max-firmware``,
  added in S1 firmware ``V40.32.015``.
"""

from __future__ import annotations

from .base import MCODE_PURIFIER_INFO, AccessoryDefinition
from .purifier import PURIFIER_ENTITIES, parse_purifier_info


LARGE_PURIFIER = AccessoryDefinition(
    type_id="LargePurifier",
    friendly_name="xTool SafetyPro AP2",
    prefix=bytes([69, 115, 96, 1, 0]),
    firmware_content_id="xTool-bigPurifier-firmware",
    info_mcode=MCODE_PURIFIER_INFO,
    parse_info=parse_purifier_info,
    entities=PURIFIER_ENTITIES,
)


LARGE_PURIFIER_V3 = AccessoryDefinition(
    type_id="LargePurifierV3",
    friendly_name="xTool SafetyPro AP2 Max",
    prefix=bytes([76, 115, 107, 1, 0]),
    firmware_content_id="xTool-BigPurifier2.0-max-firmware",
    info_mcode=MCODE_PURIFIER_INFO,
    parse_info=parse_purifier_info,
    entities=PURIFIER_ENTITIES,
)
