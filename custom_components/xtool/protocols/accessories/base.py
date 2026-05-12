"""Generic dataclasses + helpers shared by every accessory definition.

Each per-accessory module (``duct_fan.py``, ``purifier.py``, …)
imports :class:`AccessoryDefinition` + :class:`AccessoryEntitySpec`
from here and exposes its own ``DEFINITION`` constant. The
package ``__init__.py`` collects them into ``ACCESSORY_DEFINITIONS``.

Three layers of shared utilities live here:

- **Dataclasses** — :class:`AccessoryDefinition` and
  :class:`AccessoryEntitySpec` describe an accessory's protocol +
  HA entity surface.
- **Parser helpers** — :func:`num` / :func:`quoted` mirror
  Studio's ``ot()`` / ``_r()`` field extractors used by every
  M-code parser; :func:`version_only` is the stub parser for
  accessories whose detailed wire shape isn't decoded yet.
- **F0F7 framing** — :func:`encode_f0f7` / :func:`decode_f0f7`
  reproduce Studio's ``Yt`` / ``Ft`` envelope pair byte-exact.
  Used by the REST + WS-V2 ``passthrough`` / ``parts_control``
  helpers to wrap accessory M-codes.
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)


# ── BT-accessory M-codes (shared across definitions) ──────────────
#
# All M-code literals used by per-accessory definitions live here
# so adding a new accessory or tracing a wire path doesn't mean
# grepping for a half-dozen scattered strings.

# Dongle (BLE bridge)
MCODE_DONGLE_VERSION = "M9097"           # version / sn snapshot
MCODE_DONGLE_CONNECTED_LIST = "M9098"    # currently-paired accessories
MCODE_DONGLE_SCAN = "M9092"              # scan nearby BLE accessories
MCODE_DONGLE_SCAN_TOGGLE = "M9091"       # BT scan on/off (``E1``/``E0``)
MCODE_DONGLE_PAIR = "M9093"              # pair an accessory (``A<x> B1``)
MCODE_DONGLE_UNBIND = "M9112"            # unbind paired accessory

# DuctFan / AirPump (M9082-family wire shape)
MCODE_FAN_INFO = "M9082"                 # gear / sn / buzzer state
MCODE_FAN_SET_GEAR = "M9064"             # ``M9064 A<gear>``
MCODE_FAN_BUZZER = "M9079"               # ``M9079 S<0|1>``
MCODE_FAN_DUTY_CYCLE = "M9085"           # fan PWM duty

# Purifier (cabinet / AP2-class)
MCODE_PURIFIER_INFO = "M9033"            # gear + 5-filter wear state
MCODE_PURIFIER_SET_GEAR = "M9039"        # ``M9039 <gear>``
MCODE_PURIFIER_BUZZER = "M9046"          # cabinet purifier buzzer
MCODE_PURIFIER_RESET_FILTER = "M9258"    # reset filter timer

# Laser-host air-assist (S1 — wired to laser, not BT-tunneled)
MCODE_AIR_ASSIST = "M15"                 # ``M15 A<enabled> S<gear>``
MCODE_AIR_ASSIST_DELAY = "M1099"         # close-delay seconds, ``T<n>``
MCODE_AIR_ASSIST_DELAY_ALT = "M1100"     # legacy alias on some firmware

# Set of M-code heads that target the laser host directly (no F0F7
# tunnel needed). The entity dispatcher (see ``entities.py``) uses
# this to route writes through ``send_command`` instead of
# ``passthrough`` / ``parts_control``.
LASER_HOST_MCODES: tuple[str, ...] = (
    MCODE_AIR_ASSIST,
    MCODE_AIR_ASSIST_DELAY,
    MCODE_AIR_ASSIST_DELAY_ALT,
)


# ── Entity-spec primitives (declarative, platform-neutral) ──────────

@dataclass(frozen=True)
class AccessoryEntitySpec:
    """Declarative entity description.

    The entity layer maps ``platform`` to one of HA's entity
    classes (Sensor / Switch / Select / Number / Button /
    BinarySensor). ``key`` becomes the per-accessory unique-id
    suffix; ``translation_key`` defaults to ``key`` if unset.

    Write path priority (when the entity is interacted with):

    1. ``write_action`` — async callable ``(coordinator, value)
       -> None``. Used when the wire write is **not** an M-code
       (e.g. WS-V2 ``/v1/peripheral/param`` PUT, REST ``/cmd``
       POST). Bypasses the F0F7 tunnel + ``send_command`` routing
       entirely; the callable decides how to translate the value
       into a protocol-specific request.
    2. ``write_mcode`` — M-code literal or callable producing
       one. Routed by ``_passthrough_write`` through either
       ``send_command`` (for ``LASER_HOST_MCODES``) or the F0F7
       tunnel (``parts_control`` / ``passthrough``) depending on
       whether the M-code targets the laser host or a BT
       accessory.
    """

    platform: str  # "sensor" / "binary_sensor" / "switch" / "select" / "button" / "number"
    key: str
    field: str | None = None  # AccessoryState.fields["..."] lookup
    icon: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    unit: str | None = None
    entity_category: str | None = None  # "diagnostic" / "config"
    translation_key: str | None = None
    # M-code-style write (routed through F0F7 or send_command).
    write_mcode: str | Callable[[Any], str] | None = None
    # Generic write callback for non-M-code transports (V2 peripheral
    # API, REST cmd, etc.). Called with (coordinator, value).
    write_action: Callable[[Any, Any], Any] | None = None
    options: tuple[str, ...] = ()  # Select options
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None


@dataclass(frozen=True)
class AccessoryDefinition:
    type_id: str
    friendly_name: str
    prefix: bytes
    firmware_content_id: str
    info_mcode: str | None
    parse_info: Callable[[str], dict[str, Any]]
    entities: tuple[AccessoryEntitySpec, ...]


# ── M-code parser helpers ──────────────────────────────────────────

_NUMBER_RE_CACHE: dict[str, re.Pattern[str]] = {}


def num(text: str, key: str) -> float | None:
    """Mirror of Studio's ``ot(text, key)`` — extract numeric value
    following ``<key>``. Returns float or None."""
    pat = _NUMBER_RE_CACHE.get(key)
    if pat is None:
        pat = re.compile(rf"{re.escape(key)}(-?\d*\.?\d+)")
        _NUMBER_RE_CACHE[key] = pat
    m = pat.search(text)
    return float(m.group(1)) if m else None


def quoted(text: str, key: str) -> str | None:
    """Mirror of Studio's ``_r(text, key)`` — extract ``key"value"``."""
    m = re.search(re.escape(key) + r'"([^"]+)"', text)
    return m.group(1) if m else None


def version_only(text: str) -> dict[str, object]:
    """Fallback parser for accessories whose detailed M-code surface
    isn't yet derivable from the bundle (Feeder / HotStampingPen /
    UltrasonicKnife)."""
    return {
        "version": text.split(" ", 1)[0] if text else None,
        "sn": quoted(text, "E:"),
    }


# ── F0F7 framing helpers ──────────────────────────────────────────
#
# xTool BT-accessory M-codes are wrapped in a tiny F0F7 envelope
# before being base64-encoded into the ``data_b64`` field of the
# ``/passthrough`` (REST) or ``/v1/parts/control`` (WS-V2) call.
#
# Wire layout (all bytes):
#
#     0xF0  prefix(5)  cmd_utf8  0x0A  checksum  0xF7
#
# - ``prefix`` is the per-accessory-type discriminator (e.g.
#   ``[71,115,100,1,0]`` = "Gsd" for Dongle, ``[69,115,96,1,0]``
#   for Purifier; each per-accessory module declares its own).
#   Always 5 bytes for the accessories ha-xtool currently supports.
# - ``checksum = sum(prefix + cmd_utf8 + b"\n") & 0x7F``.
#
# Studio's encode is named ``Yt`` and decode is named ``Ft`` in
# the minified bundle (``/tmp/xtool-exts/<model>/index.js``);
# these reproduce both byte-exact.


def encode_f0f7(mcode: str, prefix: bytes) -> str:
    """Wrap ``mcode`` in an F0F7 frame + base64 it.

    Mirror of Studio's ``Yt({cmd, protocol:{prefix}})``. Returns the
    base64 ASCII string ready for the ``data_b64`` field of
    ``/v1/parts/control`` or ``/passthrough``.
    """
    cmd = mcode.encode("utf-8")
    checksum = sum(bytes(prefix) + cmd + b"\n") & 0x7F
    frame = (
        bytes([0xF0])
        + bytes(prefix)
        + cmd
        + b"\n"
        + bytes([checksum])
        + bytes([0xF7])
    )
    return base64.b64encode(frame).decode("ascii")


def decode_f0f7(data_b64: str, expected_mcode: str) -> str | None:
    """Decode the device's F0F7 reply and return the response payload.

    Mirror of Studio's ``Ft(data_b64, expected_mcode)``. The reply
    arrives base64-encoded with the same envelope as the request:

        0xF0  prefix(5)  <expected_mcode> <space> <fields>  0x0A  checksum  0xF7

    Returns the trimmed payload **after** stripping the leading
    ``expected_mcode`` token + surrounding whitespace, matching
    Studio's ``trim(trimStart(text, expected_mcode))``. Returns
    ``None`` if the frame is malformed, the checksum mismatches,
    or the response carries a different M-code than expected.
    """
    try:
        raw = base64.b64decode(data_b64)
    except (ValueError, TypeError) as err:
        _LOGGER.debug("F0F7 decode failed (b64): %s", err)
        return None
    if not raw or raw[0] != 0xF0:
        _LOGGER.debug("F0F7 decode: missing 0xF0 magic in %r", raw[:8])
        return None
    end = raw.find(0xF7)
    if end < 0:
        _LOGGER.debug("F0F7 decode: missing 0xF7 terminator")
        return None
    frame = raw[: end + 1]
    if len(frame) < 9:
        return None
    body_with_lf = frame[6 : len(frame) - 2]
    received_checksum = frame[len(frame) - 2]
    expected_checksum = sum(frame[1 : len(frame) - 2]) & 0x7F
    if received_checksum != expected_checksum:
        _LOGGER.debug(
            "F0F7 checksum mismatch: got 0x%02x, expected 0x%02x",
            received_checksum, expected_checksum,
        )
        return None
    text = body_with_lf.decode("utf-8", errors="replace").strip("\r\n")
    head = expected_mcode.split(" ", 1)[0]
    stripped = text.lstrip().removeprefix(head).strip()
    if stripped == "" and not text.startswith(head):
        _LOGGER.debug(
            "F0F7 response %r does not start with %r",
            text, head,
        )
        return None
    return stripped
