"""What a real xTool X1 answered, and a protocol that answers the same.

Captured 2026-11-21 from an X1 (JS001) on firmware 40.140.003.01.ht18,
idle with the lid closed and the safety key in: 55 W CO2 laser, the
diode galvo, no laser backpack, no inkjet module, the conveyor feeder
fitted, a SafetyPro AP2 and a Fire Safety Set 2 connected. The serial
numbers, device ID, UUID, MAC address, Wi-Fi name and address are
replaced.
"""

from __future__ import annotations

from typing import Any

from custom_components.xtool.protocols.ws_v2.protocol_x1 import X1WSV2Protocol

SERIAL = "MXTEST0001X1"
HOST = "192.0.2.23"
FIRMWARE = "40.140.003.01.ht18"
TASK_ID = "55ee37f1-3fbb-4c49-8ea4-c500917cbef7_LASER_PLANE_CO2_55W_VECTOR_ENGRAVING_VECTOR_CUTTING"

MACHINE_INFO = {
    "deviceCode": "JS001",
    "deviceId": "10000000000000001",
    "deviceName": "xTool_X1_000000",
    "firmware": {
        "buildTime": "2026-10-29 19:16:28",
        "modules": [
            {"appVersion": "0.3.18", "bootVersion": "0.0.3", "hardVersion": "0.0.1", "name": "MotionController"},
            {"appVersion": "0.3.18", "bootVersion": "0.0.3", "hardVersion": "0.0.1", "name": "VAxisController"},
            {"appVersion": "0.3.18", "bootVersion": "0.0.3", "hardVersion": "0.0.1", "name": "XYUAxisController"},
            {"appVersion": "0.3.18", "bootVersion": "0.0.4", "hardVersion": "0.0.1", "name": "ZAxisController"},
            {"appVersion": "0.1.0", "bootVersion": "0.0.1", "hardVersion": "0.0.1", "name": "FeederController"},
            {"appVersion": "", "bootVersion": "", "hardVersion": "1.0", "name": "MR536Controller"},
        ],
        "version": FIRMWARE,
    },
    "sku": "P0101000613",
    "sn": SERIAL,
    "uuid": "00000000-0000-0000-0000-000000000000",
}

# The push the X1 sends after each ``state/sync``.
STATE_REPORT = {
    "type": "request", "method": "REPORT", "params": {}, "timestamp": 1795299540012,
    "transactionId": 4184535408,
    "data": {"/v1/platform/device/state": {
        "automationId": "null", "gohomeState": "finish",
        "hostCustomData": (
            '{"enableAirNozzleDetect":true,"enableHeightCompensation":true,'
            '"enableCloseShot":true,"highPrecisionRanging":true,'
            '"monitorFillLightLevel":{"near":15}}'
        ),
        "mode": "Idle", "moveState": "idle", "processInfo": None, "sliceId": -1,
        "taskId": TASK_ID,
    }},
}

# While it waits for the button press that pairs a client.
SECURE_CONFIRM_REPORT = {
    **STATE_REPORT,
    "data": {"/v1/platform/device/state": {
        **STATE_REPORT["data"]["/v1/platform/device/state"], "mode": "SecureConfirm",
    }},
}

# The pairing button press.
KEY_NOTICE = {
    "type": "request", "method": "NOTIFY", "params": {}, "timestamp": 1795299539958,
    "transactionId": 4184535410,
    "data": [{"info": {"key_type": 4}, "level": "NOTICE", "name": "/v1/project/event/key", "timestamp": 385332}],
}

# Studio's Fill light left set to 0: only the light that changed.
FILL_LIGHT_REPORT = {
    "type": "request", "method": "REPORT", "params": {}, "timestamp": 1795300112336,
    "transactionId": 4184535422,
    "data": {"/v1/project/peripheral/fill-light": {"count": 1, "fillLights": [{"brightness": 0, "name": "left"}]}},
}

# The lid opened and closed (the reports come at once; lid-status follows).
LID_OPENED_REPORT = {
    "type": "request", "method": "REPORT", "params": {}, "timestamp": 1795302841000,
    "data": {"/v1/project/peripheral/lid": {"automationId": "null", "state": True}},
}
LID_CLOSED_REPORT = {
    "type": "request", "method": "REPORT", "params": {}, "timestamp": 1795302855000,
    "data": {"/v1/project/peripheral/lid": {"automationId": "null", "state": False}},
}

# Accessories: the Bluetooth dongle (71), the SafetyPro AP2 over
# Bluetooth (69) and the Fire Safety Set 2, wired (82); the rest of the
# list is not connected.
AP2_SERIAL = "MXTESTAP2"
AP2_MAC = "02:00:00:00:00:69"
FIRE_BOX_SERIAL = "MXTESTFIREBOX"
ACCESSORY_LIST = [
    {"bootloader_status": False, "common_status": "", "dev_id": 71, "name": "", "status": True,
     "version": {"app_version": "40.214.002.4736.01.B06", "boot_version": "40.214.001.4736.00.B02", "hard_version": ""}},
    {"bootloader_status": False,
     "common_status": f'M9033 A40.223.004.5222.01.B02 J0 S0 G3 T410 W1191 E:"{FIRE_BOX_SERIAL}"',
     "dev_id": 82, "name": "", "status": True,
     "version": {"app_version": "40.223.004.5222.01.B02", "boot_version": "40.223.002.5222.00.B01", "hard_version": ""}},
    {"bootloader_status": False, "common_status": "M9039 C0 D0 H98 I99 J100 K100 L100 S0", "dev_id": 69, "name": "",
     "status": True, "version": {"app_version": "40.212.007.4528.01 B03", "boot_version": "", "hard_version": "I1.1"}},
    *(
        {"bootloader_status": False, "common_status": "", "dev_id": dev_id, "name": "", "status": False,
         "version": {"app_version": "", "boot_version": "", "hard_version": ""}}
        for dev_id in (70, 78, 76)
    ),
]
# Each (accessory id, M-code) passed through /v1/platform/accessories/control, and the reply.
ACCESSORY_REPLIES: dict[tuple[int, str], Any] = {
    (69, "M9033"): {"result": f'M9033 V40.212.007.4528.01 B03 C0 D0 H98 I99 J100 K100 L100 S0 E:"{AP2_SERIAL}"\n'},
    (82, "M9033"): {"result": f'M9033 A40.223.004.5222.01.B02 J0 S0 G3 T410 W1190 E:"{FIRE_BOX_SERIAL}"\n'},
    # The X1's Bluetooth module: the AP2 paired and connected
    (71, "M9098"): {"result": f"M9098 0,{AP2_MAC},45736000,1;"},
}

# The Fire Safety Set 2 reports itself about every 2 s.
FIRE_BOX_REPORT = {
    "type": "request", "method": "REPORT", "params": {}, "timestamp": 1795301882277,
    "transactionId": 4184535597,
    "data": {"/v1/project/accessory/message": {
        "dev_id": 82, "message": f'M9033 A40.223.004.5222.01.B02 J0 S0 G3 T410 W1191 E:"{FIRE_BOX_SERIAL}"',
    }},
}

# The AP2's own report as its level was set on the purifier: Auto (C0,
# idle), levels 1-4 (A1-A4), Off (A0). Its M9039 between these carries
# the same codes.
AP2_REPORTS = {
    gear: {
        "type": "request", "method": "REPORT", "params": {},
        "data": {"/v1/project/accessory/message": {
            "dev_id": 69,
            "message": f'M9033 V40.212.007.4528.01 B03 {gear} D0 H98 I99 J100 K100 L100 S0 E:"{AP2_SERIAL}"',
        }},
    }
    for gear in ("C0", "A1", "A2", "A3", "A4", "A0")
}

# Each (method, URL) the integration sends, and the X1's reply.
REPLIES: dict[tuple[str, str], Any] = {
    ("GET", "/v1/platform/device/machine-info"): MACHINE_INFO,
    ("POST", "/v1/platform/device/state/sync"): {},
    ("GET", "/v1/project/peripheral/lid-status"): {"lidStatus": False},
    ("GET", "/v1/project/peripheral/usbkey"): {"usbKey": True},
    ("GET", "/v1/project/device/coordinate"): {"a": 30.0, "b": 30.0, "u": 0.0, "v": 0.0, "x": 0.0, "y": 0.0},
    ("GET", "/v1/platform/device/config"): {
        "beepEnable": True, "flameSensitivity": 3, "smokeFanSpeed": 750, "smokeFanTimeout": 20,
    },
    ("GET", "/v1/project/process/statistics"): {"count": 13, "singleTime": 0, "standbyTime": 18022, "totalTime": 4734},
    ("GET", "/v1/project/peripheral/fill-light"): {"count": 4, "fillLights": [
        {"brightness": 0, "name": "near"}, {"brightness": 100, "name": "left"},
        {"brightness": 100, "name": "right"}, {"brightness": 0, "name": "top"},
    ]},
    ("GET", "/v1/project/feeder/info"): {
        "block": False, "exist": True, "home": 0, "press": -1, "sn": "MXTESTFEEDER", "version": "A0.1.0 B0.0.1\n",
    },
    ("GET", "/v1/project/peripheral/feeder-status"): {"backStatus": "unblock", "exist": "exist", "frontStatus": "unblock"},
    ("GET", "/v1/project/peripheral/galvo-exist"): {"exist": True},
    ("GET", "/v1/project/peripheral/laserbag-info"): {
        "exist": False, "faultStatus": {"communicationError": "false"}, "type": "UNKNOWN",
    },
    ("GET", "/v1/project/printhead/printhead-status"): {"printhead_connected": "false", "printhead_exist": "false"},
    ("GET", "/v1/platform/wifi/connected-info"): {
        "ip": HOST, "mac_addr": "00:00:5e:00:53:01", "rssi_dbm": -55, "ssid": "example",
    },
    ("GET", "/v1/project/alarm/records"): {},
    ("GET", "/v1/platform/accessories/list"): ACCESSORY_LIST,
}


class FakeX1(X1WSV2Protocol):
    """An X1 that answers from ``REPLIES`` and records what it was sent."""

    def __init__(self, host: str, *args, **kwargs) -> None:
        super().__init__(host, *args, **kwargs)
        self.replies = dict(REPLIES)
        self.accessory_replies = dict(ACCESSORY_REPLIES)
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
        if url == "/v1/platform/accessories/control":
            return self.accessory_replies.get((params["id"], data["command"]), {"result": ""})
        reply = self.replies.get((method.upper(), url), {})
        if url == "/v1/platform/device/state/sync":
            # The X1 answers with an empty reply and pushes its state
            self._dispatch_push(STATE_REPORT)
        return reply
