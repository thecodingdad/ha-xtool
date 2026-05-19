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

# Reverse: write-mcode tail ↔ Select option, used by the set-handler
# to update ``auto_submode`` so the Select reflects the new Auto
# sub-mode immediately (M9082 poll can't distinguish Quiet/Regular).
_FANV3_WIRE_TO_OPTION: dict[str, str] = {
    wire: opt for opt, wire in _FANV3_OPTION_WIRE.items()
}


def _fanv3_option_from_state(
    mode_class: int | None,
    current_gear: int | None,
    auto_submode: str | None,
) -> str | None:
    """Derive the Select option from the M9082 ``D`` + ``B`` tokens.

    Live wire mapping (verified against if2_3.log v2.5.8 retest,
    user-supplied 14-action click trace):

    - ``D=2`` → ``"Off"`` (Manual Off; B carries last motor RPM)
    - ``D=3`` → ``str(B)`` (Manual running, B = current gear 1-4)
    - ``D=4`` → Auto running — sub-mode (Quiet vs Regular) is NOT
      derivable from the M9082 poll alone; both yield the same
      ``D=4``. Use the cached ``auto_submode`` updated by set-
      handlers and M9064 push events; fall back to ``"Auto Regular"``.
    """
    if mode_class is None:
        return None
    if mode_class == 2:
        return "Off"
    if mode_class == 3:
        if current_gear is None or not 1 <= current_gear <= 4:
            return None
        return str(current_gear)
    if mode_class == 4:
        if auto_submode in ("Auto Regular", "Auto Quiet"):
            return auto_submode
        return "Auto Regular"
    return None


def _v3_tokens(text: str, include_a: bool = False) -> dict[str, int | None]:
    """Split an M9064 / M9082 V3 wire body into letter-keyed ints.

    The version anchor (``A<…dotted…>``) only appears in the M9082
    reply, never in the M9064 push event. ``include_a=False``
    (default) skips it; pass ``include_a=True`` for the push, where
    ``A`` is a plain numeric gear echo.
    """
    keys = ("A", "B", "C", "D", "S", "Z") if include_a else (
        "B", "C", "D", "S", "Z"
    )
    out: dict[str, int | None] = {k: None for k in keys}
    for t in text.split():
        if not t or t[0] not in out or t.startswith(t[0] + ":"):
            continue
        try:
            out[t[0]] = int(t[1:])
        except ValueError:
            continue
    return out


def parse_fan_v3_info(text: str) -> dict[str, object]:
    """Decode ``DuctFanV3`` (IF2 2.0) ``M9082`` reply.

    Wire shape on F2 Ultra UV firmware ``40.130.021.00.ht2``:

    ``A<version> B<gear> C<c_state> D<mode_class> E:"<sn>" S<buzzer> Z<online>``

    Field semantics (verified live; v2.5.8 retest 14-action trace):

    - ``A`` is the firmware-version string. Contains dots — parsed
      positionally as ``tokens[0]`` to avoid colliding with B/C/D
      via a generic numeric scan.
    - ``B`` = ``current_gear`` — motor speed indicator (Manual:
      1-4 = gear; Manual Off: residual RPM of the previous gear;
      Auto: ramping speed).
    - ``C`` = ``c_state`` — alternates 2/3 across mode transitions;
      semantically unclear, kept for debugging. Earlier revs
      misnamed this ``control_mode``, which was wrong.
    - ``D`` = ``mode_class`` — authoritative mode discriminator:
      ``2`` = Manual Off, ``3`` = Manual running, ``4`` = Auto
      running. Earlier revs misnamed this ``target_gear``, which
      was wrong.
    - ``S`` = buzzer-enable flag, ``Z`` = online flag.

    ``mode_class=4`` (Auto) does NOT carry the Regular/Quiet
    sub-mode in the poll reply. The set-handler caches it in
    ``auto_submode`` on every write, and the M9064 push parser
    refreshes it on external Studio sets.
    """
    tokens = text.split()
    version = None
    if tokens and tokens[0].startswith("A"):
        version = tokens[0][1:] or None

    fields = _v3_tokens(" ".join(tokens[1:]))
    current_gear = fields["B"]
    c_state = fields["C"]
    mode_class = fields["D"]
    buzzer = fields["S"]
    connected = fields["Z"]
    return {
        "version": version,
        "current_gear": current_gear,
        "c_state": c_state,
        "mode_class": mode_class,
        "buzzer_enable": bool(buzzer) if buzzer is not None else None,
        "connected": bool(connected) if connected is not None else None,
        "sn": quoted(text, "E:"),
        # mode_speed left ``None`` here — the V2 coordinator's
        # accessory merge step calls ``derive_fan_v3_mode_speed``
        # after merging push-cached ``auto_submode``, so the Select
        # always reflects the latest sub-mode hint.
    }


def parse_fan_v3_push(text: str) -> dict[str, object]:
    """Decode a DuctFanV3 ``M9064`` ``/accessory/status`` push body.

    Push wire shape (verified live, if2_3.log v2.5.8):
    ``A<a> B<b> C<c> D<d> S<s>`` — same letter-positional convention
    as the M9082 reply but without the firmware-version anchor.

    Field semantics:

    - ``D`` = ``mode_class`` (2 / 3 / 4) — authoritative; mirrors
      the M9082 poll's ``D`` token. See ``parse_fan_v3_info``.
    - ``A`` = target / last-manual gear echo. In Manual mode the
      user-clicked gear (0 = Off, 1-4 = picked); in Auto mode
      echoes the prior Manual gear. Used to flip ``current_gear``
      immediately when ``D=3`` so the entity reacts without
      waiting for the ~600 ms M9082 poll.
    - ``B`` / ``C`` = transient state indicators; alternate 2/3
      across mode transitions, semantically unclear, kept for
      debug visibility.
    - ``S`` = buzzer-enable mirror.

    Auto sub-mode (Regular vs Quiet) is NOT recoverable from the
    push — neither the poll nor the push reliably distinguishes
    them. The set-handler caches sub-mode on every write_mcode
    fire (see ``_fanv3_set_handler`` in entities.py); external
    Studio sets will surface generically as "Auto" until the next
    HA-side write or a fresh sub-mode hint arrives.
    """
    f = _v3_tokens(text, include_a=True)
    out: dict[str, object] = {
        "mode_class": f["D"],
        "c_state": f["C"],
    }
    a_token = f["A"]
    if f["D"] == 3 and a_token is not None and 0 <= a_token <= 4:
        out["current_gear"] = a_token
    if f["S"] is not None:
        out["buzzer_enable"] = bool(f["S"])
    return out


def derive_fan_v3_mode_speed(
    fields: dict[str, object],
) -> str | None:
    """Compute ``mode_speed`` from current accessory fields.

    Called from the WS-V2 coordinator's accessory merge step after
    every poll + push update, so the Select / Fan entity surface
    the latest combined state. Reads ``mode_class``, ``current_gear``,
    and the cached ``auto_submode``.
    """
    return _fanv3_option_from_state(
        fields.get("mode_class"),  # type: ignore[arg-type]
        fields.get("current_gear"),  # type: ignore[arg-type]
        fields.get("auto_submode"),  # type: ignore[arg-type]
    )


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
