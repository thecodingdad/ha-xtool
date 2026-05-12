"""BackpackPurifier — battery-powered backpack air-cleaner.

Slightly different field layout from cabinet purifiers — the
backpack reports ``filter_status``/``filter_a``/``filter_b`` rather
than 5 individual filter wear percentages.

Prefix bytes: ``[84,115,111,1,0]`` ("Tso") — ``Iv`` in bundle.
"""

from __future__ import annotations

from .base import (
    MCODE_PURIFIER_INFO,
    AccessoryDefinition,
    AccessoryEntitySpec,
    num,
    quoted,
)


def parse_backpack_info(text: str) -> dict[str, object]:
    """Decode ``M9033`` reply (backpack purifier variant)."""
    head = text.split(" ", 1)[0]
    return {
        "version": head.replace("A", "") if head else None,
        "filter_status": num(text, "L"),
        "filter_a": num(text, "H"),
        "filter_b": num(text, "I"),
        "sn": quoted(text, "E:"),
    }


_ENTITIES = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:numeric", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "sn", field="sn",
                        icon="mdi:identifier", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "filter_status", field="filter_status",
                        icon="mdi:air-filter", unit="%"),
    AccessoryEntitySpec("sensor", "filter_a", field="filter_a",
                        icon="mdi:air-filter", unit="%"),
    AccessoryEntitySpec("sensor", "filter_b", field="filter_b",
                        icon="mdi:air-filter", unit="%"),
)


BACKPACK_PURIFIER = AccessoryDefinition(
    type_id="BackpackPurifier",
    friendly_name="xTool Backpack Purifier",
    prefix=bytes([84, 115, 111, 1, 0]),
    firmware_content_id="xTool-backpackPurifier-firmware",
    info_mcode=MCODE_PURIFIER_INFO,
    parse_info=parse_backpack_info,
    entities=_ENTITIES,
)
