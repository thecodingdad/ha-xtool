"""xTool SafetyPro AP2 + AP2 Max — five-filter HEPA purifiers.

Same M9033 wire shape + parser as the desktop AP2 cabinet. Two
size / generation variants:

- ``LargePurifier`` (``Ae`` = ``0x45``) — AP2 (Studio's internal name)
- ``LargePurifierV3`` (``Te`` / ``Ae`` = ``0x4C``) — newer AP2 Max,
  added in S1 firmware ``V40.32.015``

The AP2 uses ``[69,115,96,1,0]``; AP2 Max uses
``[76,115,107,1,0]``. Studio's marketing names live in the ``bF``
firmware-id → label table; we mirror them for the HA child-device
display.
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
