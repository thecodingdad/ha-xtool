"""DuctFan / DuctFanV3 — Smoke Purifier IF2 / IF2 2.0.

BT-paired exhaust fan that hangs off the dongle. Studio's bundle
queries ``M9082`` for state (gear, buzzer, sn, version). The IF2
sends ``M9064 ${ctr}${gear}`` to change speed; the IF2 2.0 sends
``M9064 ${ctr}${gear} C<mode>`` (see ``fan_v3_manual_mcode``).

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


# IF2 2.0 fan control, per Studio's ``setFanGearV3``:
# ``M9064 ${ctr}${gear} ${mode}`` where ``ctr`` is ``A`` (Manual) or
# ``B`` (Auto) and ``mode`` is ``C3`` (Manual), ``C4`` (Auto) or
# ``C2`` (Manual gear 0, which is Off). Without the trailing ``C``
# token the IF2 2.0 stores the gear but does not change its mode, so
# a speed set from Off leaves the fan off.
#
# Studio's Auto gear is the gear of the current processing mode
# (default 3); its quiet work mode uses one gear less
# (``_getAutoModeFanGear``). The presets below use those defaults.
FANV3_PRESET_REGULAR = "Auto Regular"
FANV3_PRESET_QUIET = "Auto Quiet"
FANV3_PRESETS: tuple[str, ...] = (FANV3_PRESET_REGULAR, FANV3_PRESET_QUIET)
FANV3_AUTO_GEAR: dict[str, int] = {
    FANV3_PRESET_REGULAR: 3,
    FANV3_PRESET_QUIET: 2,
}

# ``mode_class`` values, shared by the M9082 ``D`` token and the
# M9064 push ``C`` token.
FANV3_OFF = 2
FANV3_MANUAL = 3
FANV3_AUTO = 4


def fan_v3_manual_mcode(gear: int) -> str:
    """Return the M9064 command for Manual gear 1-4, or Off for 0."""
    gear = max(0, min(4, int(gear)))
    mode = "C3" if gear else "C2"
    return f"{MCODE_FAN_SET_GEAR} A{gear} {mode}"


def fan_v3_auto_mcode(preset: str) -> str | None:
    """Return the M9064 command for an Auto preset, or None if unknown."""
    gear = FANV3_AUTO_GEAR.get(preset)
    if gear is None:
        return None
    return f"{MCODE_FAN_SET_GEAR} B{gear} C4"


def _v3_tokens(text: str, include_a: bool = False) -> dict[str, int | None]:
    """Split an M9064 / M9082 V3 wire body into letter-keyed ints.

    The version anchor (``A<dotted version>``) only appears in the
    M9082 reply, never in the M9064 push. ``include_a=False``
    (default) skips it; pass ``include_a=True`` for the push, where
    ``A`` is a plain numeric gear.
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
    """Decode the ``DuctFanV3`` (IF2 2.0) ``M9082`` reply.

    Wire shape:
    ``A<version> B<gear> C<c> D<mode_class> E:"<sn>" S<s> Z<buzzer>``

    Field semantics follow Studio's ``getFanInfoV3``:

    - ``A`` = firmware version (contains dots, so parsed
      positionally as ``tokens[0]``).
    - ``D`` = mode: ``2`` = Manual Off, ``3`` = Manual, ``4`` = Auto.
    - ``B`` = the Manual gear, valid only when ``D=3``. In Manual Off
      it still holds the previous gear, so it is ignored there.
    - ``Z`` = buzzer enabled.

    The poll does not report the Auto gear or whether Auto is the
    regular or the quiet preset; those come from the M9064 push and
    from HA's own writes, and the coordinator carries them across
    polls.
    """
    tokens = text.split()
    version = None
    if tokens and tokens[0].startswith("A"):
        version = tokens[0][1:] or None

    fields = _v3_tokens(" ".join(tokens[1:]))
    mode_class = fields["D"]
    gear = fields["B"]
    buzzer = fields["Z"]
    out: dict[str, object] = {
        "version": version,
        "mode_class": mode_class if mode_class in (FANV3_OFF, FANV3_MANUAL, FANV3_AUTO) else None,
        "buzzer_enable": bool(buzzer) if buzzer is not None else None,
        # The M9082 reply itself proves the accessory is connected.
        "connected": True,
        "sn": quoted(text, "E:"),
    }
    if mode_class == FANV3_MANUAL and gear is not None and 1 <= gear <= 4:
        out["current_gear"] = gear
    elif mode_class == FANV3_OFF:
        out["current_gear"] = 0
    return out


def parse_fan_v3_push(text: str) -> dict[str, object]:
    """Decode a ``DuctFanV3`` ``M9064`` ``/accessory/status`` push body.

    Wire shape: ``A<gear> B<auto_gear> C<mode> D<job> S<alert>``, for
    example ``M9064 A4 B3 C4 D1 S0`` (Auto, a job running at gear 3).
    Field semantics follow Studio's M9064 report handler and captures
    from an F2 Ultra UV:

    - ``C`` = mode: ``2`` = Manual Off, ``3`` = Manual, ``4`` = Auto.
    - ``A`` = the Manual gear (kept while in Auto or Off).
    - ``B`` = the gear Auto runs at. A job sets it from its G-code
      (``G198 P78 "M9064 B<n>"``) and it drops to 0 after the job's
      post-run time; choosing a preset sets it while idle.
    - ``D`` = ``1`` from job start until the post-run time ends,
      otherwise ``0``. In Auto the fan spins only then.
    - ``S`` = alerts: ``1`` fan stall, ``2`` cleaning reminder,
      ``3`` both. Kept as ``alert`` for the Alert event entity.
    """
    f = _v3_tokens(text, include_a=True)
    out: dict[str, object] = {}
    mode = f["C"]
    if mode in (FANV3_OFF, FANV3_MANUAL, FANV3_AUTO):
        out["mode_class"] = mode
        gear = f["A"]
        if mode == FANV3_OFF:
            out["current_gear"] = 0
        elif mode == FANV3_MANUAL and gear is not None and 1 <= gear <= 4:
            out["current_gear"] = gear
        auto_gear = f["B"]
        if mode == FANV3_AUTO and auto_gear is not None and 0 <= auto_gear <= 4:
            out["auto_gear"] = auto_gear
            # The IF2 does not report the preset itself. Studio runs
            # Auto at the processing mode's gear (3 by default) and one
            # gear less when quiet, so gears 1-2 read as quiet and 3-4
            # as regular. Gear 0 (idle after a job) keeps the last one.
            if 1 <= auto_gear <= 2:
                out["auto_submode"] = FANV3_PRESET_QUIET
            elif auto_gear >= 3:
                out["auto_submode"] = FANV3_PRESET_REGULAR
    if f["D"] is not None:
        out["job_active"] = f["D"] == 1
    if f["S"] is not None:
        out["alert"] = f["S"]
    return out


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
                        write_mcode=lambda on: f"{MCODE_FAN_BUZZER} S{1 if on else 0}",
                        entity_category="config"),
)


# IF2 2.0 (``DuctFanV3``) entity spec. One HA fan entity covers the
# whole fan control: off, Manual gears 1-4 as percentage, and the two
# Auto presets (class ``_AccessoryFan`` in ``entities.py``).
_ENTITIES_V3 = (
    AccessoryEntitySpec("sensor", "version", field="version",
                        icon="mdi:numeric", entity_category="diagnostic"),
    AccessoryEntitySpec(
        "fan", "fan", field="mode_class",
        icon="mdi:fan",
    ),
    AccessoryEntitySpec("switch", "buzzer", field="buzzer_enable",
                        write_mcode=lambda on: f"{MCODE_FAN_BUZZER} S{1 if on else 0}",
                        entity_category="config"),
    # Fan stall / cleaning needed, from the M9064 push ``S`` bits
    # (``field=None``: it builds before the first push carries them).
    AccessoryEntitySpec("event", "alert", icon="mdi:fan-alert"),
    # Inline-fan post-run timer (read-only). Studio's
    # ``setFanV3RunDuration`` hard-wires ``M9085 T0`` and never
    # exposes a user-facing slider on the IF2 side — the value is
    # authored on the laser via ``purifierTimeout`` (see the
    # F-family "Exhaust time after processing" Number) and mirrored
    # to the IF2 via the ``smokingFanDelay`` push. Exposing it as a
    # read-only Sensor here so users can verify the mirror without
    # having a second writable control that would fight the
    # laser-side source of truth.
    AccessoryEntitySpec(
        "sensor", "post_run", field="post_run_seconds",
        icon="mdi:fan-clock", unit="s",
        entity_category="diagnostic",
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
    # Replaced by the single fan entity.
    retired_entities=(("select", "mode_speed"), ("sensor", "current_speed")),
)
