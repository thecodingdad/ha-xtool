"""FireExtinguisher + V1.5 + SafetyFireBoxPro.

Detailed info wire shape not yet derivable from the bundle — ships
as a **stub** (sn + fw version only) until a user with the
accessory provides a debug-log capture.
"""

from __future__ import annotations

from .base import (
    AccessoryDefinition,
    AccessoryEntitySpec,
    version_only,
)


_ENTITIES = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:numeric", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "sn", field="sn",
                        icon="mdi:identifier", entity_category="diagnostic"),
)


# Placeholder prefix bytes — same as DuctFan until logs reveal the
# real BT discriminator.
_PREFIX = bytes([70, 115, 99, 1, 0])


FIRE_EXTINGUISHER = AccessoryDefinition(
    type_id="FireExtinguisher",
    friendly_name="xTool Fire Safety Set",
    prefix=_PREFIX,
    firmware_content_id="xTool-extinguisherBox-firmware",
    info_mcode=None,
    parse_info=version_only,
    entities=_ENTITIES,
)


FIRE_EXTINGUISHER_V1_5 = AccessoryDefinition(
    type_id="FireExtinguisherV1_5",
    friendly_name="xTool Fire Safety Set v1.5",
    prefix=_PREFIX,
    firmware_content_id="xTool-extinguisherBox-firmware",
    info_mcode=None,
    parse_info=version_only,
    entities=_ENTITIES,
)


SAFETY_FIRE_BOX_PRO = AccessoryDefinition(
    type_id="SafetyFireBoxPro",
    friendly_name="xTool SafetyFireBoxPro",
    prefix=_PREFIX,
    firmware_content_id="xTool-SafetyFireBoxPro-firmware",
    info_mcode=None,
    parse_info=version_only,
    entities=_ENTITIES,
)
