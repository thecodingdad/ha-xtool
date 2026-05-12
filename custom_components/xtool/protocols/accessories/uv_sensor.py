"""UvSensor — ``uv_sensor_wb031`` ("firesense_hub").

External BT-paired UV flame detector — distinct from a laser's
built-in ``uv_fire_sensor`` peripheral. Stub-only (sn + fw
version) until logs reveal the live-status M-code.
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


UV_SENSOR = AccessoryDefinition(
    type_id="UvSensor",
    friendly_name="xTool Firesense Hub",
    prefix=bytes([70, 115, 99, 1, 0]),
    firmware_content_id="xTool-uvSensor-firmware",
    info_mcode=None,
    parse_info=version_only,
    entities=_ENTITIES,
)
