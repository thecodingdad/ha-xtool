"""An xTool O1 Omni (DT002) that answers as the O1 in issue #22 did.

The read replies have the shapes a live O1 (UV & DT edition, firmware
40.101.013.01.ht1) answered, see docs/PROTOCOL.md; the fill light and
the config read have the shapes Studio's DT002 bundle reads. Serial
numbers, MAC address and Wi-Fi name are made up.
"""

from __future__ import annotations

import json
from typing import Any

from custom_components.xtool.protocols.ws_v2.protocol_dt002 import DT002WSV2Protocol

SERIAL = "DTTEST0001O1"
HOST = "192.0.2.31"
FIRMWARE = "40.101.013.01.ht1"
TASK_ID = "6b1f0c2e-0000-0000-0000-00000000o1"

MACHINE_INFO = {
    "deviceCode": "DT002",
    "deviceId": "10000000000000002",
    "deviceName": "xTool Omni Printer O1",
    "sn": SERIAL,
    "sku": 3,
    "uuid": "00000000-0000-0000-0000-000000000002",
    "firmware": {
        "version": FIRMWARE,
        "buildTime": "2026-09-01 10:00:00",
        "modules": [{"name": "MR536", "appVersion": "1.0.0", "bootVersion": "1.0.0", "hardVersion": "V1.0"}],
    },
}


def _cartridge(tank_id: int, serial: str, full_ml: float = 125) -> dict[str, Any]:
    return {
        "tank_id": tank_id, "exist": True, "serial": serial, "full_ml": full_ml,
        "remain_ml": full_ml * 0.8, "level": 0, "level_value": 80,
        "calib_status": 0, "calib_value": 0,
    }


INK_TANK = {
    "inkRoad1": [
        _cartridge(i, f"XT0000U{c}0001") for i, c in enumerate("CMYKVW")
    ],
    "inkRoad2": [
        *(_cartridge(i, f"XT0000D{c}0001") for i, c in enumerate("CMYK")),
        _cartridge(4, "XT0000DS0001", 290),
        {"tank_id": 5, "exist": False, "serial": "", "full_ml": -1, "remain_ml": -1, "level": 0},
    ],
    "whiteStireStatus": {"active": False, "remainSec": 0},
}

MAINTENANCE_TANK = {
    "moisturizing": [
        {"index": 0, "exist": True, "serial": "XT0000UF0001", "full_ml": 125, "remain_ml": 100, "level": 0},
        {"index": 1, "exist": True, "serial": "XT0000DF0001", "full_ml": 125, "remain_ml": 100, "level": 0},
    ],
    "waste": {"exist": True, "level": 0, "level_value": 10, "calib_status": 0, "calib_value": 0},
}

REPLIES: dict[tuple[str, str], Any] = {
    ("GET", "/v1/platform/device/machine-info"): MACHINE_INFO,
    ("GET", "/v1/platform/wifi/connected-info"): {
        "ip": HOST, "mac_addr": "02:00:00:00:00:31", "rssi_dbm": -52, "ssid": "test",
    },
    ("GET", "/v1/project/device/peripheral-status"): {
        "doors": [{"type": 1, "status": False}, {"type": 2, "status": False}],
        "lid": False, "filter": True, "pallet": 1,
    },
    ("GET", "/v1/project/device/ink-tank"): INK_TANK,
    ("GET", "/v1/project/device/maintenance-tank"): MAINTENANCE_TANK,
    ("GET", "/v1/project/device/temp-humity"): {"temp": 24.5, "humity": 41},
    ("GET", "/v1/project/device/printer-head-param"): {
        "power_error": 0,
        "printer_head_param": [
            {"ink_road": 1, "ink_type": 0, "temp": 31.0, "work_state": 1, "exit_state": True},
            {"ink_road": 2, "ink_type": 1, "temp": 27.5, "work_state": 1, "exit_state": True},
        ],
    },
    ("GET", "/v1/project/task/list"): {
        "print_tasks": [], "history_tasks": [], "totalPrintTaskCount": 0, "totalHistoryTaskCount": 0,
    },
    ("GET", "/v1/project/device/config/all"): json.dumps({
        "processCount": 12, "processTotalTime": 3600, "standbyTime": 7200, "beepEnable": True,
    }),
    ("GET", "/v1/project/device/fill-light"): {
        "on": False, "brightness": 0, "autoOffSec": 600, "remainSec": 0, "suppressed": False,
    },
    ("GET", "/v1/platform/device/config"): {"fillLightAutoOffTime": 600},
}


def report(url: str, payload: Any) -> dict[str, Any]:
    return {"type": "request", "method": "REPORT", "params": {}, "data": {url: payload}}


def notify(name: str, info: Any) -> dict[str, Any]:
    return {"type": "request", "method": "NOTIFY", "params": {}, "data": [{"name": name, "info": info, "level": "NOTICE"}]}


def process(action: str, task_id: str = TASK_ID) -> dict[str, Any]:
    return {
        "type": "request", "method": "NOTIFY-RECORD", "params": {},
        "data": [{"name": "/v1/platform/device/process", "content": {"action": action, "taskId": task_id}}],
    }


def mode(name: str) -> dict[str, Any]:
    return report("/v1/platform/device/state", {"mode": name})


PRINTING_TASK = {
    "id": TASK_ID, "name": "mug.xcs", "status": "PRINTING", "type": "PRINT",
    "processingMode": "UV_PRINT", "currentPrintPass": 10, "totalPrintPass": 40,
    "processingTime": 60, "estimateTime": 240, "index": 0,
}


class FakeO1(DT002WSV2Protocol):
    """An O1 that answers from ``REPLIES`` and records what it was sent."""

    def __init__(self, host: str, *args, **kwargs) -> None:
        super().__init__(host, *args, **kwargs)
        self.replies = dict(REPLIES)
        self.sent: list[tuple[str, str, dict | None, dict | None]] = []

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def request(
        self,
        url: str,
        method: str = "GET",
        params: dict | None = None,
        data: dict | None = None,
        timeout: float = 10.0,
        _skip_connect: bool = False,
    ) -> Any:
        self.sent.append((method.upper(), url, params, data))
        return self.replies.get((method.upper(), url), {})

    def push(self, *events: dict[str, Any]) -> None:
        """Pushes, as the reader loop hands them on."""
        for event in events:
            self._dispatch_push(event)
            if self._push_notify is not None:
                self._push_notify()

    def writes(self) -> list[tuple[str, str, dict | None, dict | None]]:
        return [sent for sent in self.sent if sent[0] != "GET"]
