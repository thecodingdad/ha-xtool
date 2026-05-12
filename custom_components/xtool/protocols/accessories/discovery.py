"""``M9098`` connected-accessory list parser.

The dongle's ``getAllDangleConnectList`` command (``M9098``)
returns one row per currently-connected accessory. Studio
normalises rows into ``{partsId, snCode, status}``; the
implementation here keeps each row's raw tokens so a future log
capture can drive further decoding without a re-flash.
"""

from __future__ import annotations

from .base import MCODE_DONGLE_CONNECTED_LIST, num, quoted


def parse_connected_list(text: str) -> list[dict[str, object]]:
    """Decode ``M9098`` reply — list of currently-connected
    accessories per dongle."""
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip().rstrip("\n")
        if not line or line.startswith(MCODE_DONGLE_CONNECTED_LIST):
            continue
        rows.append({
            "raw": line,
            "type_id_raw": num(line, "A"),
            "status": num(line, "B"),
            "sn": quoted(line, "E:"),
        })
    return rows
