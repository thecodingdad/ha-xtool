"""What a real xTool S1 answered, and a protocol that answers the same.

Captured 2026-11-20 from the maintainer's S1 (main firmware
V40.32.015.2025.01 B10, laser V40.32.009.2122.01 B1, Wi-Fi
V40.32.013.2224.01 B1; 2 W infrared module, riser base, an Air Pump in
slot 2 and a Fire Extinguisher v1.5 in slot 4), idle with the fill light
on, from the integration's debug log. Only the serial number is replaced.
"""

from __future__ import annotations

from custom_components.xtool.protocols.s1.protocol import S1Protocol

SERIAL = "MXTEST0001"

M2003 = (
    'M2003{"M54":"T1","M345":"S1","M222":"S3","M99":"V40.32.015.2025.01 B10",'
    '"M1199":"V40.32.009.2122.01 B1","M2099":"V40.32.013.2224.01 B1",'
    '"M1098":["","","V40.208.003.3D28.01 B1","","V40.203.002.3224.01B01","","","","",""],'
    f'"M97":"S0","M310":"{SERIAL}","M98":"X-0.40 Y19.88","M100":"xTool S1",'
    '"M116":"X1Y2B0P0L0","M120":"A1.5","M321":"S0","M13":"A100 B100",'
    '"M27":"X0.000 Y99.840 Z0.000 U0.000","M105":"X0.00Y0.00Z0.00"}'
)

M1098 = 'M1098 "","","V40.208.003.3D28.01 B1","","V40.203.002.3224.01B01","","","","",""'

# Each command the integration sends, and the S1's reply.
REPLIES: dict[str, str] = {
    "M2003": M2003,
    "M100": 'M100 "xTool S1"',
    "M223": "M223 X498.00 Y319.00 Z58.00",
    "M222": "M222 S3",
    "M13": "M13 A100 B100",
    "M340": "M340 A0",
    "M343": "M343 S0",
    "M7": "M7 S0 N1 D120",
    "M21": "M21 S1",
    "M318": "M318 N1",
    "M1099": "M1099 T10",
    "M15": "M15 A1 S0",
    "M27": "M27 X0.000 Y99.840 Z0.000 U0.000",
    "M810": 'M810 "NULL"',
    "M815": "M815 T0",
    "M321": "M321 S0",
    "M1098": M1098,
    "M54": "M54 T1",
    "M2008 A1": "M2008 A54252 B171 C863241 D5267",
}


class FakeS1(S1Protocol):
    """An S1 that answers from ``REPLIES`` and records what it was sent."""

    def __init__(self, host: str, *args, **kwargs) -> None:
        super().__init__(host, *args, **kwargs)
        self.replies = dict(REPLIES)
        self.sent: list[str] = []
        self._open = False

    @property
    def connected(self) -> bool:
        return self._open

    async def connect(self) -> None:
        self._open = True

    async def disconnect(self) -> None:
        self._open = False

    async def send_command(self, command: str, timeout: float = 5.0) -> str:
        self.sent.append(command)
        return self.replies.get(command, "")

    async def get_connection_count(self) -> int:
        return 1

    async def _http_get(self, path: str, timeout: float = 5.0) -> str | None:
        return None
