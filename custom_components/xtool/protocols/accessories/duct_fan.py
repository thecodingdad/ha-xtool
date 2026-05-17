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
    MCODE_FAN_RUN_DURATION,
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


# Studio bundle's airflow-adjustment picker collapses three modes
# and the manual gear ladder into one UI element. Mirror the same
# flat option list as a single HA Select entity. Wire mapping per
# Studio's setFanGear({ctr, gear}) → ``M9064 ${ctr}${gear}``:
#
# - Manual: ctr="A", gear 0/1/2/3/4
# - Auto Regular: ctr="B", gear 3 (Studio's auto-regular preset)
# - Auto Quiet:   ctr="B", gear 1 (Studio's auto-quiet preset)
FANV3_MODE_OPTIONS: tuple[str, ...] = (
    "Auto Regular", "Auto Quiet", "Off", "1", "2", "3", "4",
)

_FANV3_OPTION_WIRE: dict[str, str] = {
    "Auto Regular": "B3",
    "Auto Quiet":   "B1",
    "Off":          "A0",
    "1":            "A1",
    "2":            "A2",
    "3":            "A3",
    "4":            "A4",
}


def _fanv3_option_from_state(
    control_mode: int | None, current_gear: int | None,
) -> str | None:
    """Derive the Select option from the M9082 ``C`` + ``B`` tokens.

    - ``C=0`` (Manual): ``Off`` (B=0) or ``str(B)`` (B=1-4)
    - ``C≥1`` (Auto): map B=3 → Auto Regular, B=1 → Auto Quiet;
      anything else falls back to ``Auto Regular`` as a safe default.
    """
    if control_mode is None:
        return None
    if control_mode == 0:
        if current_gear is None:
            return None
        if current_gear == 0:
            return "Off"
        if 1 <= current_gear <= 4:
            return str(current_gear)
        return None
    # Auto modes
    if current_gear == 1:
        return "Auto Quiet"
    return "Auto Regular"


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
    - ``C`` is the control mode (0 = Manual, ≥ 1 = Auto).
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
        # Combined mode + gear picker for the single-Select entity.
        "mode_speed": _fanv3_option_from_state(control_mode, current_gear),
    }


def _fanv3_mode_speed_mcode(option: str) -> str | None:
    """Map a Select option to the matching ``M9064 <ctr><gear>``."""
    wire = _FANV3_OPTION_WIRE.get(option)
    if wire is None:
        return None
    return f"{MCODE_FAN_SET_GEAR} {wire}"


# Legacy IF2 v1 (``DuctFan``) entity spec. Kept narrow — the V1
# wire shape only carries gear (str) + buzzer; no mode / current-
# speed separation.
_ENTITIES_V1 = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:numeric", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "gear", field="gear",
                        icon="mdi:fan-speed-1"),
    AccessoryEntitySpec("select", "gear_select", field="gear",
                        icon="mdi:fan", options=("0", "1", "2", "3"),
                        write_mcode=lambda gear: f"{MCODE_FAN_SET_GEAR} A{gear}"),
    AccessoryEntitySpec("switch", "buzzer", field="buzzer_enable",
                        icon="mdi:bell-ring",
                        write_mcode=lambda on: f"{MCODE_FAN_BUZZER} S{1 if on else 0}",
                        entity_category="config"),
)


# IF2 2.0 (``DuctFanV3``) entity spec. Studio's UI collapses the
# Mode + Manual-gear pickers into a single flat option list; mirror
# that here.
_ENTITIES_V3 = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:numeric", entity_category="diagnostic"),
    AccessoryEntitySpec("sensor", "current_speed", field="current_gear",
                        icon="mdi:fan-speed-1"),
    AccessoryEntitySpec(
        "select", "mode_speed", field="mode_speed",
        icon="mdi:fan",
        options=FANV3_MODE_OPTIONS,
        write_mcode=_fanv3_mode_speed_mcode,
        entity_category="config",
    ),
    # HA-native Fan-domain wrapper around the same wire path —
    # surfaces ``preset_modes`` + ``percentage`` for users who
    # prefer the Fan card over a flat 7-option dropdown. Both
    # entities co-exist; either UI drives the device through
    # ``M9064 <mode><gear>``. Class lives in
    # ``protocols/accessories/entities.py:_AccessoryFan`` —
    # ``field="mode_speed"`` is just the skip-if-missing guard.
    AccessoryEntitySpec(
        "fan", "fan", field="mode_speed",
        icon="mdi:fan",
        entity_category="config",
    ),
    AccessoryEntitySpec("switch", "buzzer", field="buzzer_enable",
                        icon="mdi:bell-ring",
                        write_mcode=lambda on: f"{MCODE_FAN_BUZZER} S{1 if on else 0}",
                        entity_category="config"),
    # Inline-fan post-run timer (``M9085 T<seconds>``). Studio's
    # ``setFanV3RunDuration`` route writes the same M-code. Value is
    # mirrored from the laser-host ``smokingFanDelay`` push, which the
    # coordinator merges into ``fields['post_run_seconds']`` for the
    # paired IF2 2.0 accessory.
    AccessoryEntitySpec(
        "number", "post_run", field="post_run_seconds",
        icon="mdi:fan-clock", unit="s",
        min_value=0, max_value=300, step=5,
        write_mcode=lambda v: f"{MCODE_FAN_RUN_DURATION} T{int(v)}",
        entity_category="config",
    ),
)


DUCT_FAN = AccessoryDefinition(
    type_id="DuctFan",
    friendly_name="xTool SafetyPro IF2",
    prefix=bytes([70, 115, 99, 1, 0]),
    firmware_content_id="xTool-ductFan-firmware",
    info_mcode=MCODE_FAN_INFO,
    parse_info=parse_fan_info,
    entities=_ENTITIES_V1,
)


DUCT_FAN_V3 = AccessoryDefinition(
    type_id="DuctFanV3",
    friendly_name="xTool SafetyPro IF2 2.0",
    prefix=bytes([78, 115, 99, 1, 0]),
    firmware_content_id="xTool-ductFan2.0-firmware",
    info_mcode=MCODE_FAN_INFO,
    parse_info=parse_fan_v3_info,
    entities=_ENTITIES_V3,
)
