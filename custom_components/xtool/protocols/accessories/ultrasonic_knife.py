"""UltrasonicKnife — M1 Ultra tool head. Stub until logs arrive."""

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


ULTRASONIC_KNIFE = AccessoryDefinition(
    type_id="UltrasonicKnife",
    friendly_name="xTool Ultrasonic Knife",
    prefix=bytes([70, 115, 99, 1, 0]),
    firmware_content_id="",
    info_mcode=None,
    parse_info=version_only,
    entities=_ENTITIES,
)
