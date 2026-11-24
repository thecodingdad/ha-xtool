"""M1 Ultra tool heads: the IDs its firmware reports for the fitted tools.

Names follow xTool's: a quick-swap module (laser module, multi-function
carriage or inkjet module), the tool in the basic carriage (fine-point
pen, fine-point blade, hot foil pen) and the blade in the multi-function
carriage (cutting blade, rotary blade, foil transfer tip).
``workhead_ID`` reports the module (``driving``) and the basic carriage
tool (``drived``); with the multi-function carriage fitted,
``knife_head`` reports its blade (``driving``) once it was synced. The
IDs were read on hardware (V1 and V2 firmware).
"""

from __future__ import annotations

from typing import Any

# Option for an ID this table does not know yet (the raw reply is in the
# debug log).
OTHER = "other"

TOOL_HEAD_IDS: dict[int, str] = {
    0: "none",
    15: "laser_10w",
    16: "laser_20w",
    29: "multi_function_carriage",
    31: "inkjet",
}
BASIC_CARRIAGE_IDS: dict[int, str] = {
    41: "none",
    42: "fine_point_pen",
    43: "fine_point_blade",
    44: "hot_foil_pen",
}
BLADE_IDS: dict[int, str] = {
    0: "none",
    22: "foil_transfer_tip",
    23: "cutting_blade",
    24: "rotary_blade",
}

TOOL_HEAD_OPTIONS = [*dict.fromkeys(TOOL_HEAD_IDS.values()), OTHER]
BASIC_CARRIAGE_OPTIONS = [*dict.fromkeys(BASIC_CARRIAGE_IDS.values()), OTHER]
BLADE_OPTIONS = [*dict.fromkeys(BLADE_IDS.values()), OTHER]

MULTI_FUNCTION_CARRIAGE = "multi_function_carriage"


def _payload(reply: Any) -> dict[str, Any] | None:
    """The fields of a reply; the V1 REST reply nests them in ``data``."""
    if not isinstance(reply, dict):
        return None
    inner = reply.get("data")
    return inner if isinstance(inner, dict) else reply


def _lookup(table: dict[int, str], value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return table.get(int(value), OTHER)
    except (TypeError, ValueError):
        return OTHER


def parse_workhead(reply: Any) -> tuple[str | None, str | None]:
    """``(tool head, multi-function tool)`` from a ``workhead_ID`` reply."""
    p = _payload(reply)
    if p is None:
        return None, None
    return (
        _lookup(TOOL_HEAD_IDS, p.get("driving")),
        _lookup(BASIC_CARRIAGE_IDS, p.get("drived")),
    )


def parse_blade(reply: Any) -> str | None:
    """The blade in the multi-function carriage from a ``knife_head`` reply."""
    p = _payload(reply)
    if p is None:
        return None
    return _lookup(BLADE_IDS, p.get("driving"))
