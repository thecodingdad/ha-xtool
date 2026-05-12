"""BT accessory subsystem.

Generic per-accessory framework shared by every protocol family.
xTool BT accessories (Smoke Purifier IF2/IF2 2.0, Air Pump,
Cabinet/Backpack Purifier, UV Sensor, FireExtinguisher, Dongle,
M1 Ultra tool heads, …) hang off the laser device over a
UART485-tunneled BLE link. Studio talks to them via:

- ``POST /v1/parts/control`` body
  ``{link:"uart485", data_b64: <F0F7-framed M-code>}`` — WS-V2
- ``POST /passthrough`` (port 8080) body
  ``{link:"uart485", data_b64: <F0F7-framed M-code>}`` — REST V1
- raw M-code over the WS — S1 (no F0F7 wrapper needed)

Discovery: ``M9098`` (parsed in :mod:`discovery`) returns the list
of currently-connected accessories per dongle. Per-type info
M-codes (``M9082`` for fan, ``M9033`` for purifier, …) refresh
accessory state.

Each per-accessory module owns its M-code parser + entity surface
and exposes its :class:`AccessoryDefinition` constants. This
``__init__`` assembles them into ``ACCESSORY_DEFINITIONS``; to add
a new accessory, drop a sibling module + add one line to
``_ALL_DEFINITIONS`` below.

See ``docs/PROTOCOL.md`` § "BT accessory subsystem" for the
authoritative wire-level reference.
"""

from __future__ import annotations

from .air_pump import AIR_PUMP, AIR_PUMP_V2
from .backpack_purifier import BACKPACK_PURIFIER
from .base import (
    AccessoryDefinition,
    AccessoryEntitySpec,
    decode_f0f7,
    encode_f0f7,
)
from .discovery import parse_connected_list
from .dongle import DONGLE
from .duct_fan import DUCT_FAN, DUCT_FAN_V3
from .feeder import FEEDER
from .fire_extinguisher import (
    FIRE_EXTINGUISHER,
    FIRE_EXTINGUISHER_V1_5,
    SAFETY_FIRE_BOX_PRO,
)
from .hot_stamping_pen import HOT_STAMPING_PEN
from .multi_base import MULTI_FUNCTIONAL_BASE
from .large_purifier import LARGE_PURIFIER, LARGE_PURIFIER_V3
from .purifier import PURIFIER
from .ultrasonic_knife import ULTRASONIC_KNIFE
from .uv_sensor import UV_SENSOR

_ALL_DEFINITIONS: tuple[AccessoryDefinition, ...] = (
    DUCT_FAN,
    DUCT_FAN_V3,
    PURIFIER,
    LARGE_PURIFIER,
    LARGE_PURIFIER_V3,
    BACKPACK_PURIFIER,
    AIR_PUMP,
    AIR_PUMP_V2,
    DONGLE,
    FIRE_EXTINGUISHER,
    FIRE_EXTINGUISHER_V1_5,
    SAFETY_FIRE_BOX_PRO,
    UV_SENSOR,
    MULTI_FUNCTIONAL_BASE,
    FEEDER,
    HOT_STAMPING_PEN,
    ULTRASONIC_KNIFE,
)


ACCESSORY_DEFINITIONS: dict[str, AccessoryDefinition] = {
    d.type_id: d for d in _ALL_DEFINITIONS
}


def get_definition(type_id: str) -> AccessoryDefinition | None:
    """Lookup an accessory definition by its type id."""
    return ACCESSORY_DEFINITIONS.get(type_id)


__all__ = [
    "ACCESSORY_DEFINITIONS",
    "AccessoryDefinition",
    "AccessoryEntitySpec",
    "decode_f0f7",
    "encode_f0f7",
    "get_definition",
    "parse_connected_list",
]
