"""``M9098`` connected-accessory list parser.

The dongle's ``getAllDangleConnectList`` command (``M9098``)
returns one row per currently-connected accessory in one of two
wire shapes:

1. **Token form** — ``A<typeId> B<status> E:"<sn>"`` per row,
   newline-separated. Used by the older firmware revs that emit
   the raw Studio bundle tokens straight back.
2. **CSV form** — ``num,mac,type_hex,status;num,mac,type_hex,status;…``
   semicolon-separated rows with four comma-separated fields.
   Used by current V2 firmware (F2 Ultra UV, F1 Ultra V2 …).
   ``type_hex`` is the F0F7 prefix bytes hex-encoded; its first
   two characters are the Te-enum value (one byte). ``mac`` may
   actually be the accessory's MAC address (e.g.
   ``38:36:0C:01:C1:96``) — we keep it as the per-accessory SN
   because nothing else in the row uniquely identifies the
   device.

The parser auto-detects the shape per row.
"""

from __future__ import annotations

from .base import MCODE_DONGLE_CONNECTED_LIST, num, quoted


def parse_connected_list(text: str) -> list[dict[str, object]]:
    """Decode ``M9098`` reply — list of currently-connected
    accessories per dongle. Handles both the legacy token form
    and the V2-firmware CSV form."""
    rows: list[dict[str, object]] = []
    # Strip the M-code prefix once; the CSV form arrives on a
    # single line with semicolon row-separators (no newlines).
    stripped = text.strip()
    if stripped.startswith(MCODE_DONGLE_CONNECTED_LIST):
        stripped = stripped[len(MCODE_DONGLE_CONNECTED_LIST):].lstrip()

    # Split on both newlines *and* semicolons so either wire shape
    # yields one row per element.
    raw_rows: list[str] = []
    for chunk in stripped.splitlines():
        for piece in chunk.split(";"):
            piece = piece.strip()
            if piece:
                raw_rows.append(piece)

    for line in raw_rows:
        # CSV form: exactly 4 comma-separated fields and no
        # ``E:"…"`` SN token. Anything else falls through to the
        # token-form parser so legacy replies still work.
        parts = [p.strip() for p in line.split(",")]
        if (
            len(parts) == 4
            and 'E:"' not in line
            and "A" not in parts[0][:2]
        ):
            num_str, mac_or_sn, type_hex, status_str = parts
            type_id_raw: float | None = None
            if len(type_hex) >= 2:
                try:
                    type_id_raw = float(int(type_hex[:2], 16))
                except ValueError:
                    type_id_raw = None
            try:
                status: float | None = float(int(status_str))
            except ValueError:
                status = None
            rows.append({
                "raw": line,
                "type_id_raw": type_id_raw,
                "status": status,
                "sn": mac_or_sn or None,
            })
            continue

        # Legacy token form.
        rows.append({
            "raw": line,
            "type_id_raw": num(line, "A"),
            "status": num(line, "B"),
            "sn": quoted(line, "E:"),
        })
    return rows
