"""MultiFunctionalBase — M1 Ultra multi-tool base.

Wire shape variant of M9082 / M9033 — info-only stub for now.
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


MULTI_FUNCTIONAL_BASE = AccessoryDefinition(
    type_id="MultiFunctionalBase",
    friendly_name="xTool MultiFunctional Base",
    prefix=bytes([70, 115, 99, 1, 0]),
    firmware_content_id="xTool-multiFunctionalBase-firmware",
    info_mcode=None,
    parse_info=version_only,
    entities=_ENTITIES,
)
