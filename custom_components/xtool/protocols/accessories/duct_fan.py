"""DuctFan / DuctFanV3 — Smoke Purifier IF2 / IF2 2.0.

BT-paired exhaust fan that hangs off the dongle. Studio's bundle
queries ``M9082`` for state (gear, buzzer, sn, version) and sends
``M9064 ${ctr}${gear}`` to change speed.

Prefix bytes:

- ``DuctFan``    = ``[70,115,99,1,0]``  ("Fsc")  — `e0` in bundle
- ``DuctFanV3``  = ``[78,115,99,1,0]``  ("Nsc")  — `yi` in bundle
"""

from __future__ import annotations

from .base import (
    MCODE_FAN_BUZZER,
    MCODE_FAN_INFO,
    MCODE_FAN_SET_GEAR,
    MCODE_PURIFIER_RESET_FILTER,
    AccessoryDefinition,
    AccessoryEntitySpec,
    num,
    quoted,
)


def parse_fan_info(text: str) -> dict[str, object]:
    """Decode ``M9082`` reply.

    Wire shape (after F0F7 strip + M-code prefix removed):
    ``<v1> <v2> A<gear> C<ctrl> Z<buzzer> E:"<sn>"``
    """
    parts = text.split(" ", 2)
    version = " ".join(parts[:2]) if len(parts) >= 2 else None
    c_val = num(text, "C")
    a_val = num(text, "A")
    z_val = num(text, "Z")
    return {
        "version": version,
        "gear": "0" if c_val == 2 else (str(int(a_val)) if a_val is not None else None),
        "gear_control": "B" if c_val == 4 else "A",
        "buzzer_enable": z_val == 1 if z_val is not None else None,
        "sn": quoted(text, "E:"),
    }


_ENTITIES = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:numeric", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "sn", field="sn",
                        icon="mdi:identifier", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "gear", field="gear",
                        icon="mdi:fan-speed-1"),
    AccessoryEntitySpec("binary_sensor", "buzzer_enable",
                        field="buzzer_enable",
                        icon="mdi:volume-high",
                        entity_category="diagnostic"),
    AccessoryEntitySpec("select", "gear_select", field="gear",
                        icon="mdi:fan", options=("0", "1", "2", "3"),
                        write_mcode=lambda gear: f"{MCODE_FAN_SET_GEAR} A{gear}"),
    AccessoryEntitySpec("switch", "buzzer", field="buzzer_enable",
                        icon="mdi:bell-ring",
                        write_mcode=lambda on: f"{MCODE_FAN_BUZZER} S{1 if on else 0}"),
    AccessoryEntitySpec("button", "reset_filter",
                        icon="mdi:filter-remove",
                        write_mcode=f"{MCODE_PURIFIER_RESET_FILTER} A0"),
)


DUCT_FAN = AccessoryDefinition(
    type_id="DuctFan",
    friendly_name="xTool SafetyPro IF2",
    prefix=bytes([70, 115, 99, 1, 0]),
    firmware_content_id="xTool-ductFan-firmware",
    info_mcode=MCODE_FAN_INFO,
    parse_info=parse_fan_info,
    entities=_ENTITIES,
)


DUCT_FAN_V3 = AccessoryDefinition(
    type_id="DuctFanV3",
    friendly_name="xTool SafetyPro IF2 2.0",
    prefix=bytes([78, 115, 99, 1, 0]),
    firmware_content_id="xTool-ductFan2.0-firmware",
    info_mcode=MCODE_FAN_INFO,
    parse_info=parse_fan_info,
    entities=_ENTITIES,
)
