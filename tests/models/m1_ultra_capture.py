"""What a real xTool M1 Ultra answered, and a request handler that answers the same.

Captured 2026-11-24 from an M1 Ultra (MLM) on firmware
40.41.018.2020.01.ht4 (WS-V2), idle with the lid closed and the safety
key in, while the quick-swap module, the basic carriage tool and the
blade were swapped. The serial number is replaced.
"""

from __future__ import annotations

from typing import Any

SERIAL = "MLMTEST0001M1U"
FIRMWARE = "40.41.018.2020.01.ht4"

RUNTIME_INFOS = {
    "alarmInfo": {"desc": "The system runs all the warnings that have occurred"},
    "cpuTemp": 43,
    "curAlarmInfo": {"desc": "Warnings that exist while the system is running"},
    "curMode": {
        "desc": "",
        "mode": "P_IDLE",
        "subMode": "",
        "taskId": "9191c58a-a369-4ba2-ae97-badcc1c6865b",
    },
    "devTime": "2026-11-24 15:06:13",
}

# Asked for every key Studio's M1 Ultra extension lists; printToolType
# never comes back, whichever tools are fitted.
CONFIGS = {
    "airassistCut": 4,
    "airassistGrave": 3,
    "beepEnable": False,
    "flameAlarm": True,
    "machineLockCheck": True,
    "purifierTimeout": 20,
    "workingMode": "HANDLE",
}

# /v1/peripheral/param?type=workhead_ID with {action: "get"}:
# multi-function carriage and fine-point blade fitted.
WORKHEAD_MULTI_FUNCTION_CARRIAGE = {"drived": 43, "driving": 29, "drivingError": 0, "drivingLock": 0}
# The blade before and after Sync blade (rotary blade fitted).
KNIFE_HEAD_NOT_SYNCED = {"driving": 0}
KNIFE_HEAD_ROTARY_BLADE = {"driving": 24}

# Sync blade's refusal until the axes are homed (after a module swap);
# the blade read was refused with code 1 then, too.
NOT_HOMED = "code 1: reset is interrupt, can't get_sync kniftHead."

# Pushed while one module is swapped for another.
SWAP_PUSHES = tuple(
    {"url": "/laser_head/status", "data": {"module": "LASER_HEAD", "type": "VALUE_CHANGE", "info": info}}
    for info in ("drivingUnLock", "driving", "drivingLock")
)


class FakeM1Ultra:
    """Answers the WS-V2 requests as the captured M1 Ultra did."""

    def __init__(self) -> None:
        self.mode = "P_IDLE"
        self.cpu_temp: int = RUNTIME_INFOS["cpuTemp"]
        self.workhead: dict[str, Any] = dict(WORKHEAD_MULTI_FUNCTION_CARRIAGE)
        self.knife_head: dict[str, Any] = dict(KNIFE_HEAD_NOT_SYNCED)
        self.homed = True
        self.requests: list[tuple[str, str, dict | None, dict | None]] = []

    async def __call__(self, url, method="GET", params=None, data=None, **kwargs):
        self.requests.append((url, method, params, data))
        if url == "/v1/device/runtime-infos":
            return {
                **RUNTIME_INFOS,
                "cpuTemp": self.cpu_temp,
                "curMode": {**RUNTIME_INFOS["curMode"], "mode": self.mode},
            }
        if url == "/v1/device/configs":
            return dict(CONFIGS)
        if url == "/v1/peripheral/param":
            ptype = params["type"]
            if ptype == "workhead_ID":
                return dict(self.workhead)
            if ptype == "knife_head":
                if not self.homed:
                    raise RuntimeError(f"V2 {method} /v1/peripheral/param returned {NOT_HOMED}")
                if method == "PUT" and data == {"action": "get_sync"}:
                    return {}
                return dict(self.knife_head)
            return {"state": "on"}
        return {}

    def peripheral_reads(self, ptype: str) -> list[dict | None]:
        return [
            data for url, method, params, data in self.requests
            if url == "/v1/peripheral/param" and method == "GET" and params == {"type": ptype}
        ]
