"""What a real xTool Apparel Printer (DT001) answered, and a protocol that answers the same.

Captured 2026-11-06 by corillis (#36) from an Apparel Printer on firmware
40.100.028.00.ht6 with an OS1 oven and a SafetyPro AP2: the replies of
its read routes and pushes, and series of them while a lid, the waste
tank, the oven and a nozzle clean changed (``dt001_capture.json``).
Serial numbers and addresses are redacted.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from custom_components.xtool.protocols.ws_v2.protocol_dt001 import DT001WSV2Protocol

CAPTURE: dict[str, Any] = json.loads((Path(__file__).parent / "dt001_capture.json").read_text())
HOST = "192.0.2.41"
SERIAL = "DTTEST0001DT001"

ROUTES = (
    "/v1/device/runtime-infos", "/v1/peripheral/ink_bottle", "/v1/peripheral/water_bottle",
    "/v1/device/roaster", "/v1/device/workingInfo", "/v1/device/configs",
)


class FakeDT001(DT001WSV2Protocol):
    """A DT001 that answers its read routes from the capture and records what it was sent."""

    def __init__(self, host: str = HOST, *args, **kwargs) -> None:
        super().__init__(host, *args, **kwargs)
        self.replies: dict[str, Any] = {route: copy.deepcopy(CAPTURE[route]) for route in ROUTES}
        self.sent: list[tuple[str, str]] = []
        self._connected = True

    async def connect(self) -> None:
        self._connected = True

    async def request(
        self,
        url: str,
        method: str = "GET",
        params: dict | None = None,
        data: dict | None = None,
        timeout: float = 10.0,
        _skip_connect: bool = False,
    ) -> Any:
        self.sent.append((method.upper(), url))
        reply = self.replies.get(url, {})
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)
