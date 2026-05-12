"""Dongle — BLE bridge module.

Acts as the BT relay between every other paired accessory and the
laser's UART485. ``M9097`` returns the dongle's own firmware
version + serial; the coordinator-side ``M9098`` enumeration uses
this dongle's prefix to talk to attached accessories.

Prefix bytes: ``[71,115,100,1,0]`` ("Gsd") — ``dr`` in bundle.
"""

from __future__ import annotations

from .base import (
    MCODE_DONGLE_VERSION,
    AccessoryDefinition,
    AccessoryEntitySpec,
    quoted,
)


def parse_dongle_version(text: str) -> dict[str, object]:
    """Decode ``M9097`` reply — dongle firmware version + sn."""
    return {
        "version": text.split(" ", 1)[0] if text else None,
        "sn": quoted(text, "E:"),
    }


_ENTITIES = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:bluetooth", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "sn", field="sn",
                        icon="mdi:identifier", entity_category="diagnostic"),
)


DONGLE = AccessoryDefinition(
    type_id="Dongle",
    friendly_name="xTool Bluetooth Dongle",
    prefix=bytes([71, 115, 100, 1, 0]),
    firmware_content_id="xTool-dongle-firmware",
    info_mcode=MCODE_DONGLE_VERSION,
    parse_info=parse_dongle_version,
    entities=_ENTITIES,
)
