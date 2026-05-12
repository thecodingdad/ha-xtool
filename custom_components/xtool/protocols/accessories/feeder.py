"""Feeder — M1 Ultra material feeder. Stub until logs arrive."""

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


FEEDER = AccessoryDefinition(
    type_id="Feeder",
    friendly_name="xTool Feeder",
    prefix=bytes([70, 115, 99, 1, 0]),
    firmware_content_id="xTool-feeder-firmware",
    info_mcode=None,
    parse_info=version_only,
    entities=_ENTITIES,
)
