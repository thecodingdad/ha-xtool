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
    AccessoryDefinition,
    AccessoryEntitySpec,
    num,
    quoted,
)


def parse_fan_info(text: str) -> dict[str, object]:
    """Decode legacy ``DuctFan`` (IF2 v1) ``M9082`` reply.

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


def parse_fan_v3_info(text: str) -> dict[str, object]:
    """Decode ``DuctFanV3`` (IF2 2.0) ``M9082`` reply.

    Wire shape on F2 Ultra UV firmware ``40.130.021.00.ht2``
    (verified from a live capture):

    ``A<version> B<gear> C<mode> D<target> E:"<sn>" S<buzzer> Z<connected>``

    where:

    - ``A`` carries the full firmware-version string (contains
      dots — must be parsed positionally, not via the generic
      ``num()`` helper which would otherwise also match the
      ``B02`` substring inside the version).
    - ``B`` is the current motor speed (0-4 in Manual mode, an
      empirical 0-100 PWM-like reading in Auto modes).
    - ``C`` is the control mode (0 = Manual, 1 = Auto-Regular,
      2 = Auto-Quiet — inferred from Studio bundle behaviour).
    - ``D`` is the most recently selected manual gear / preset
      anchor.
    - ``S`` is the buzzer flag, ``Z`` the online flag.
    """
    tokens = text.split()
    version = None
    if tokens and tokens[0].startswith("A"):
        version = tokens[0][1:] or None

    def _tok_int(prefix: str) -> int | None:
        for t in tokens[1:]:
            if t.startswith(prefix) and not t.startswith(prefix + ':'):
                try:
                    return int(t[len(prefix):])
                except ValueError:
                    return None
        return None

    current_gear = _tok_int("B")
    control_mode = _tok_int("C")
    target_gear = _tok_int("D")
    buzzer = _tok_int("S")
    connected = _tok_int("Z")
    return {
        "version": version,
        "current_gear": current_gear,
        "control_mode": control_mode,
        "target_gear": target_gear,
        "buzzer_enable": bool(buzzer) if buzzer is not None else None,
        "connected": bool(connected) if connected is not None else None,
        "sn": quoted(text, "E:"),
        # Back-compat alias for the legacy ``gear`` field still
        # referenced by the shared entity spec until the v2.5.4 IF2
        # entity-layout refactor lands.
        "gear": str(current_gear) if current_gear is not None else None,
    }


_ENTITIES = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:numeric", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "sn", field="sn",
                        icon="mdi:identifier", entity_category="diagnostic"),
    # ``current_speed`` reads the live ``B`` token of the M9082 reply
    # (the actual motor speed, identical to the gear value in Manual
    # mode and an empirical 0-100 PWM-like value in Auto modes).
    AccessoryEntitySpec("sensor", "current_speed", field="current_gear",
                        icon="mdi:fan-speed-1"),
    # ``manual_gear`` writes via ``M9064 A<n>``. 0 = off, 1-4 = manual
    # gears. Matches Studio's "Manual / OFF / 1 / 2 / 3 / 4" gear
    # picker. The legacy ``gear_select`` (single-select 0-3) and the
    # ``reset_filter`` button were removed in v2.5.4 — the gear
    # range is wider (0-4) on the V3 protocol and the filter-reset
    # action does not surface a button in Studio for the IF2 family.
    AccessoryEntitySpec("number", "manual_gear", field="current_gear",
                        icon="mdi:fan", unit=None,
                        min_value=0, max_value=4, step=1,
                        write_mcode=lambda gear: f"{MCODE_FAN_SET_GEAR} A{int(gear)}",
                        entity_category="config"),
    AccessoryEntitySpec("switch", "buzzer", field="buzzer_enable",
                        icon="mdi:bell-ring",
                        write_mcode=lambda on: f"{MCODE_FAN_BUZZER} S{1 if on else 0}",
                        entity_category="config"),
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
    parse_info=parse_fan_v3_info,
    entities=_ENTITIES,
)
