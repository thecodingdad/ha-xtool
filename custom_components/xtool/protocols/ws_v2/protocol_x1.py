"""WS-V2 protocol dialect for the xTool X1 (JS001).

The X1 is a 55 W CO2 gantry with a flying galvo head, swappable laser
modules (diode galvo, a fiber / MOPA / UV backpack, an inkjet module),
three cameras and an optional conveyor feeder. It speaks xTool's
platform 2.0 interface like the M2: ``REPORT`` pushes,
``/v1/platform/device/*`` (machine-info, state/sync, flat config), the
process statistics, the camera snap and ``/v1/project/device/control``
for jobs. Those come from :class:`M2WSV2Protocol`; the X1's own
hardware is read here, as Studio's X1 extension (``xcs-ext-js001``)
reads it.

Event-like pushes arrive as frames whose ``data`` is a list of
``{name, level, info, content}`` notices; the job lifecycle comes from
the ``/v1/platform/device/process`` notices in them.

Checked on an X1 with firmware 40.140.003.01.ht18.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from ...const import XtoolStatus
from ..accessories.discovery import parse_connected_list
from ..base import XtoolDeviceState
from .protocol_m2 import (
    M2_PATH_CONFIG,
    M2_PATH_COORDINATE,
    M2_PATH_DEVICE_CONTROL,
    M2_PATH_PROCESS_EVENT,
    M2_PATH_STATE,
    M2_PATH_STATE_SYNC,
    M2WSV2Protocol,
)

_LOGGER = logging.getLogger(__name__)

X1_PATH_LID = "/v1/project/peripheral/lid"
X1_PATH_LID_STATUS = "/v1/project/peripheral/lid-status"
X1_PATH_USB_KEY = "/v1/project/peripheral/usbkey"
X1_PATH_FILL_LIGHT = "/v1/project/peripheral/fill-light"
X1_PATH_FEEDER_INFO = "/v1/project/feeder/info"
X1_PATH_FEEDER_STATUS = "/v1/project/peripheral/feeder-status"
X1_PATH_FEEDER_SENSOR = "/v1/project/device/feeder-sensor"  # push only
X1_PATH_FEEDER_PRESS = "/v1/project/peripheral/feeder-press"
X1_PATH_FEEDER_HOME = "/v1/project/peripheral/feeder-home"

# Studio's feeder clamp (feederPress): it presses down to
# -(29 + hardness - thickness) at speed 4, the hardness 2 for rigid and 0
# for flexible material; thicknesses up to 68.9 (25.4 + 43.5).
X1_FEEDER_HARDNESS = {"rigid": 2, "flexible": 0}
X1_FEEDER_MAX_THICKNESS = 68.9
X1_PATH_GALVO_EXIST = "/v1/project/peripheral/galvo-exist"
X1_PATH_LASER_BAG = "/v1/project/peripheral/laserbag-info"
X1_PATH_PRINTHEAD = "/v1/project/printhead/printhead-status"
X1_PATH_WIFI = "/v1/platform/wifi/connected-info"
X1_PATH_ALARM_RECORDS = "/v1/project/alarm/records"
X1_PATH_ALARM = "/v1/platform/device/alarm"  # notice only
X1_PATH_ACCESSORIES = "/v1/platform/accessories/list"
X1_PATH_ACCESSORY_CONTROL = "/v1/platform/accessories/control"
X1_PATH_ACCESSORY_MESSAGE = "/v1/project/accessory/message"  # push only

# The X1's Bluetooth module, reached as an accessory. Studio's accessory
# window: scan on (180 s), the found list read again and again, connect
# by MAC, then the paired list shows it connected; scan off on closing.
X1_BLUETOOTH_ID = 71
BLUETOOTH_PAIRED = "M9098"
BLUETOOTH_SCAN_ON = "M9091 E1 D180"
BLUETOOTH_SCAN_OFF = "M9091 E0"
BLUETOOTH_FOUND = "M9092 T5000"
BLUETOOTH_CONNECT = "M9093 A{mac} B1"
BLUETOOTH_SEARCH_SECONDS = 120.0
BLUETOOTH_CONNECT_SECONDS = 20.0
BLUETOOTH_POLL_SECONDS = 2.0

# Studio's X1 alarm code for a fire (``nd.FIRE``)
X1_ALARM_FIRE = 1001

# Every setting Studio's X1 extension reads (its ``getConfigs`` key list).
X1_SETTINGS = (
    "smokeFanTimeout", "smokeFanSpeed", "beepEnable", "distCircleU",
    "feederRadius", "automationId", "dirtyCheckEnable", "dirtyCheckTime",
    "dirtyUncleanTotalTime", "dirtyLowFrequencyCheckEnable",
    "dirtyLowFrequencyCheckTime", "dirtyLowFrequencyUncleanTotalTime",
    "mdMode", "automationEnable", "laserDotCo2OffsetX", "laserDotCo2OffsetY",
    "laserBlueFramePower", "flameSensitivity", "hostCustomData",
    "laserDotSelfCheckTime", "focusDetectControlEnable", "feederYScan",
    "curGalvoLensType",
    "vibrationCalibDurationGalvoInkjet", "vibrationCalibDurationGalvoNoInkjet",
    "vibrationCalibDurationNoGalvoInkjet", "vibrationCalibDurationNoGalvoNoInkjet",
    "vibrationCalibBaseGalvoInkjet", "vibrationCalibBaseGalvoNoInkjet",
    "vibrationCalibBaseNoGalvoInkjet", "vibrationCalibBaseNoGalvoNoInkjet",
)
# The settings with an entity, read with a key list as Studio does (the
# X1 answers ``in_data is not array`` to a read without one).
X1_CONFIG_KEYS = [
    "smokeFanTimeout", "smokeFanSpeed", "beepEnable", "flameSensitivity",
    "focusDetectControlEnable",
]
_X1_CONFIG_FIELDS = (
    ("smokeFanTimeout", "smoking_fan_duration"),
    ("smokeFanSpeed", "exhaust_fan_speed"),
    ("beepEnable", "beep_enabled_v2"),
    ("flameSensitivity", "flame_sensitivity"),
    ("focusDetectControlEnable", "trial_run_check"),
)
# The entities write the F family's names for the shared settings.
_X1_CONFIG_KEY_NAMES = {"purifierTimeout": "smokeFanTimeout"}

# Studio's X1 mode enum. ``SecureConfirm`` is reported while the laser
# waits for its button (the pairing confirmation); Studio counts it as
# not busy, with Idle (``isDeviceBusy``).
X1_MODE_MAP: dict[str, XtoolStatus] = {
    "Initial": XtoolStatus.INITIALIZING,
    "Idle": XtoolStatus.IDLE,
    "SecureConfirm": XtoolStatus.IDLE,
    "Busy": XtoolStatus.WORKING_API,
    "Measure": XtoolStatus.MEASURING,
    "Leveling": XtoolStatus.MEASURING,
    "Calibration": XtoolStatus.MEASURING,
    "Autowork": XtoolStatus.PROCESSING,
    "WorkReady": XtoolStatus.PROCESSING_READY,
    "WorkPlay": XtoolStatus.PROCESSING,
    "WorkPause": XtoolStatus.PAUSED,
    "Sleep": XtoolStatus.SLEEPING,
    "Upgrade": XtoolStatus.FIRMWARE_UPDATE,
    "Network": XtoolStatus.WIFI_SETUP,
    "Error": XtoolStatus.ERROR_LIMIT,
}

# Studio's pausePrint / resumePrint / cancelPrint.
_X1_JOB_ACTIONS: dict[str, str] = {
    "start": "RESUME",
    "pause": "PAUSE",
    "stop": "CANCEL",
}

# Job events from the process notices: Studio's process actions (its
# ``Xg`` enum) start, cancel and finish. Pause and resume follow from
# the state push.
_X1_PROCESS_EVENTS: dict[str, str] = {
    "start": "started",
    "cancel": "cancelled",
    "finish": "finished",
}

# Laser backpack ``type`` → sensor state (Studio's badges B1-B4: 5 W UV,
# 30 W fiber, 60 W MOPA, 5 W UV + 60 W MOPA).
_X1_BACKPACKS: dict[str, str] = {
    "RED_30W": "fiber_30w",
    "RED_60W": "mopa_60w",
    "UV": "uv_5w",
    "UV_RED_60W": "uv_mopa",
}

# The fill lights Studio sets, and the state fields that hold them.
_X1_FILL_LIGHT_FIELDS = {
    "left": "fill_light_left",
    "right": "fill_light_right",
    "near": "fill_light_near",
}


def _flag(value: Any) -> bool:
    """A yes/no the way Studio's X1 extension reads it (``"true"``, ``"1"``, true)."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return bool(value)


class X1WSV2Protocol(M2WSV2Protocol):
    """The X1: the platform 2.0 interface with the X1's own hardware."""

    _LATEST_STATE_FIELDS = M2WSV2Protocol._LATEST_STATE_FIELDS + (
        "fill_light_left", "fill_light_right", "fill_light_near",
        "feeder_installed", "feeder_front_blocked", "feeder_rear_blocked",
        "diode_laser_installed", "laser_backpack", "inkjet_module_installed",
        "exhaust_fan_speed", "flame_sensitivity", "trial_run_check",
        "bluetooth_accessories_disconnected",
    )

    # Studio's X1 config key list: no ``gapCheck`` (Cover check).
    SETTINGS = frozenset(X1_SETTINGS)

    # Studio updates the X1 with its own requests, not the F family's
    # ``/v1/device/upgrade-mode`` that ``flash_firmware`` sends.
    FIRMWARE_INSTALL = False

    # Settings, modules and statistics change rarely: every sixth poll.
    _SLOW_POLL_EVERY = 6

    def __init__(self, host: str, port: int | None = None) -> None:
        super().__init__(host, port)
        self._x1_poll_counter = 0
        # (accessory id, M-code report) from the accessory message pushes,
        # until the coordinator applies them
        self.pending_accessory_messages: list[tuple[int, str]] = []

    # --- Polling -----------------------------------------------------------

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Status, lid, safety key and position every poll; the rest every sixth."""
        if not self._connected:
            await self.connect()

        # The reply is empty: the sync makes the laser push its state.
        try:
            await self.request(M2_PATH_STATE_SYNC, method="POST")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("X1 %s failed: %s", M2_PATH_STATE_SYNC, err)

        lid = await self._get_dict(X1_PATH_LID_STATUS)
        if isinstance(lid.get("lidStatus"), bool):
            self._latest["cover_open"] = lid["lidStatus"]
        key = await self._get_dict(X1_PATH_USB_KEY)
        if isinstance(key.get("usbKey"), bool):
            self._latest["machine_lock"] = key["usbKey"]
        coordinate = await self._get_dict(M2_PATH_COORDINATE)
        for axis in ("x", "y"):
            if isinstance(coordinate.get(axis), (int, float)):
                self._latest[f"position_{axis}"] = float(coordinate[axis])

        if self._x1_poll_counter % self._SLOW_POLL_EVERY == 0:
            await self._poll_slow(state)
        self._x1_poll_counter += 1

        self._apply_latest_to_state(state)

    async def _poll_slow(self, state: XtoolDeviceState) -> None:
        config = await self._get_dict(M2_PATH_CONFIG, data={"kv": X1_CONFIG_KEYS})
        self._apply_x1_configs(config)
        await self._poll_m2_statistics(state)
        self._apply_fill_lights(await self._get_dict(X1_PATH_FILL_LIGHT))

        feeder = await self._get_dict(X1_PATH_FEEDER_INFO)
        if "exist" in feeder:
            self._latest["feeder_installed"] = _flag(feeder["exist"])
        sensors = await self._get_dict(X1_PATH_FEEDER_STATUS)
        self._apply_feeder_sensors(sensors)

        galvo = await self._get_dict(X1_PATH_GALVO_EXIST)
        if "exist" in galvo:
            self._latest["diode_laser_installed"] = _flag(galvo["exist"])
        backpack = await self._get_dict(X1_PATH_LASER_BAG)
        if "exist" in backpack:
            self._latest["laser_backpack"] = (
                _X1_BACKPACKS.get(str(backpack.get("type")))
                if _flag(backpack["exist"]) else "none"
            )
        printhead = await self._get_dict(X1_PATH_PRINTHEAD)
        if "printhead_exist" in printhead:
            self._latest["inkjet_module_installed"] = _flag(printhead["printhead_exist"])

        wifi = await self._get_dict(X1_PATH_WIFI)
        if isinstance(wifi.get("rssi_dbm"), int):
            self._latest["wifi_signal"] = wifi["rssi_dbm"]

        paired = await self.bluetooth_accessories()
        if paired is not None:
            self._latest["bluetooth_accessories_disconnected"] = sum(
                1 for _, connected in paired if not connected
            )

        # Studio's getAlarmRecords: the alarms still active, then the
        # history. The X1 answers {} when there are none.
        alarms = await self._get_dict(X1_PATH_ALARM_RECORDS, timeout=10.0)
        active = alarms.get("alarms-list")
        self._latest["alarm_present"] = bool(active) if isinstance(active, list) else False

    async def _get_dict(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            reply = await self.request(url, "GET", **kwargs)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("X1 %s failed: %s", url, err)
            return {}
        return reply if isinstance(reply, dict) else {}

    def _apply_x1_configs(self, config: dict[str, Any]) -> None:
        for key, field in _X1_CONFIG_FIELDS:
            if key in config:
                self._latest[field] = config[key]

    def _apply_fill_lights(self, reply: dict[str, Any]) -> None:
        """``{count, fillLights: [{name, brightness}]}``, read or pushed."""
        lights = reply.get("fillLights")
        if not isinstance(lights, list):
            return
        for light in lights:
            if not isinstance(light, dict):
                continue
            field = _X1_FILL_LIGHT_FIELDS.get(str(light.get("name")))
            if field is not None and isinstance(light.get("brightness"), int):
                self._latest[field] = light["brightness"]

    def _apply_feeder_sensors(self, reply: dict[str, Any]) -> None:
        """``block`` or ``unblock``: ``frontStatus`` / ``backStatus`` when
        read, ``frontSensor`` / ``backSensor`` when pushed."""
        for keys, field in (
            (("frontStatus", "frontSensor"), "feeder_front_blocked"),
            (("backStatus", "backSensor"), "feeder_rear_blocked"),
        ):
            for key in keys:
                if isinstance(reply.get(key), str):
                    self._latest[field] = reply[key] == "block"

    # --- Accessories ---------------------------------------------------------

    async def platform_accessories(self) -> list[dict[str, Any]]:
        """The laser's accessory list: ``[{dev_id, status, version, …}]``.

        ``status`` is true for the connected ones, Bluetooth and wired.
        """
        reply = await self.request(X1_PATH_ACCESSORIES, "GET", timeout=10.0)
        return [entry for entry in reply if isinstance(entry, dict)] if isinstance(reply, list) else []

    async def accessory_command(self, accessory_id: int, mcode: str) -> str | None:
        """Pass an M-code to an accessory, as Studio's platform 2.0 path does.

        Returns the reply without its leading M-code (``M9033 A40…`` →
        ``A40…``), or None when there is none.
        """
        reply = await self.request(
            X1_PATH_ACCESSORY_CONTROL, "POST",
            params={"id": accessory_id}, data={"command": mcode}, timeout=10.0,
        )
        result = reply.get("result") if isinstance(reply, dict) else None
        if not isinstance(result, str) or not result.strip():
            return None
        return result.strip().removeprefix(mcode.split(" ", 1)[0]).strip() or None

    # --- Bluetooth accessories ---------------------------------------------

    async def bluetooth_accessories(self) -> list[tuple[str, bool]] | None:
        """The accessories paired with the X1's Bluetooth module.

        ``M9098`` answers ``<n>,<MAC>,<type>,<1 connected / 0 not>;`` per
        accessory: (MAC, connected) each; None when it did not answer.
        """
        try:
            reply = await self.accessory_command(X1_BLUETOOTH_ID, BLUETOOTH_PAIRED)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("X1 %s failed: %s", BLUETOOTH_PAIRED, err)
            return None
        return [
            (str(row["sn"]), row["status"] == 1)
            for row in parse_connected_list(reply or "")
            if row.get("sn")
        ]

    async def reconnect_bluetooth_accessories(self) -> tuple[int, int]:
        """Connect the paired accessories the X1 does not reach, as Studio does.

        Studio's accessory window: Bluetooth scan on, and each accessory
        connected (``M9093 A<MAC> B1``) once it shows up in the found
        list (``M9092``); the paired list (``M9098``) then shows it
        connected. One that comes back by itself counts too. An
        accessory that lost its own pairing shows up only in its
        pairing mode (the AP2: its power button held for 5 s). Returns
        (not connected before, connected now).
        """
        paired = await self.bluetooth_accessories() or []
        missing = {mac for mac, connected in paired if not connected}
        if not missing:
            self._latest["bluetooth_accessories_disconnected"] = 0
            return 0, 0
        before = len(missing)
        asked: dict[str, float] = {}
        await self.accessory_command(X1_BLUETOOTH_ID, BLUETOOTH_SCAN_ON)
        try:
            deadline = time.monotonic() + BLUETOOTH_SEARCH_SECONDS
            while missing and time.monotonic() < deadline:
                for mac, connected in await self.bluetooth_accessories() or []:
                    if connected:
                        missing.discard(mac)
                if not missing:
                    break
                found = await self.accessory_command(X1_BLUETOOTH_ID, BLUETOOTH_FOUND) or ""
                for mac in sorted(missing):
                    # Asked again only when the last connect came to nothing
                    if mac in found and time.monotonic() - asked.get(mac, -BLUETOOTH_CONNECT_SECONDS) >= BLUETOOTH_CONNECT_SECONDS:
                        asked[mac] = time.monotonic()
                        reply = await self.accessory_command(
                            X1_BLUETOOTH_ID, BLUETOOTH_CONNECT.format(mac=mac),
                        )
                        _LOGGER.debug("X1 Bluetooth connect %s: %s", mac, reply)
                await asyncio.sleep(BLUETOOTH_POLL_SECONDS)
        finally:
            with contextlib.suppress(Exception):
                await self.accessory_command(X1_BLUETOOTH_ID, BLUETOOTH_SCAN_OFF)
        self._latest["bluetooth_accessories_disconnected"] = len(missing)
        return before, before - len(missing)

    # --- Writes ------------------------------------------------------------

    async def set_config(self, key: str, value: Any) -> dict[str, Any]:
        """PUT one setting as Studio does: a flat body, booleans as booleans."""
        key = _X1_CONFIG_KEY_NAMES.get(key, key)
        result = await self.request(M2_PATH_CONFIG, "PUT", data={key: value})
        # Kept as the laser's own: the settings are read every sixth poll.
        self._apply_x1_configs({key: value})
        return result

    async def set_named_fill_light(self, name: str, brightness: int) -> dict[str, Any]:
        """Studio's setFillLight: ``PUT fill-light?name=<name>`` with ``{brightness}``."""
        result = await self.request(
            X1_PATH_FILL_LIGHT, "PUT",
            params={"name": name}, data={"brightness": brightness},
        )
        self._latest[_X1_FILL_LIGHT_FIELDS[name]] = brightness
        return result

    def feeder_refusal(self) -> str | None:
        """Why the feeder may not move now, as Studio's guards: fitted, laser idle."""
        if not self._latest.get("feeder_installed"):
            return "no feeder is fitted"
        if self._latest.get("status") != XtoolStatus.IDLE:
            return "the laser is not idle"
        return None

    async def feeder_release(self) -> None:
        """Studio's Release material (feederRelease)."""
        await self.request(X1_PATH_FEEDER_HOME, "POST")

    async def feeder_clamp(self, thickness: float, material: str) -> None:
        """Studio's clamp for a material thickness (feederPress)."""
        distance = -(29 + X1_FEEDER_HARDNESS[material] - thickness)
        await self.request(
            X1_PATH_FEEDER_PRESS, "POST", data={"distance": round(distance, 2), "speed": 4},
        )

    async def set_processing_state(self, action: str) -> dict[str, Any]:
        return await self.request(
            M2_PATH_DEVICE_CONTROL,
            method="POST",
            params={"action": _X1_JOB_ACTIONS[action]},
        )

    async def set_peripheral(
        self, peripheral_type: str, action: str | None = None, **extra: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            f"X1 has no {peripheral_type!r} peripheral control"
        )

    # --- Pushes ------------------------------------------------------------

    def _dispatch_push(self, event: dict[str, Any]) -> None:
        """Route a push by its shape, as Studio's X1 extension does.

        A list holds event notices (``NOTIFY``); an object maps paths to
        their state (``REPORT``) or to an event's content. The method
        name does not matter to Studio.
        """
        data = event.get("data")
        if isinstance(data, list):
            for notice in data:
                if isinstance(notice, dict):
                    self._dispatch_x1_notice(notice)
            return
        if isinstance(data, dict) and "url" not in event:
            for url, payload in data.items():
                if url in (M2_PATH_PROCESS_EVENT, X1_PATH_ALARM):
                    # Studio's handleEventFromObject
                    content = payload.get("content") if isinstance(payload, dict) else None
                    self._dispatch_x1_notice(
                        {"name": url, "content": content} if content else {"name": url, "info": payload},
                    )
                else:
                    self._dispatch_m2_report(url, payload)
            return
        super()._dispatch_push(event)

    def _dispatch_x1_notice(self, notice: dict[str, Any]) -> None:
        """One ``{name, level, info, content}`` entry of a NOTIFY frame."""
        name = notice.get("name")
        if name == X1_PATH_FEEDER_SENSOR and isinstance(notice.get("info"), dict):
            self._apply_feeder_sensors(notice["info"])
            return
        if name == X1_PATH_ALARM:
            self._dispatch_x1_alarms(notice.get("info"))
            return
        if name != M2_PATH_PROCESS_EVENT:
            _LOGGER.debug("X1 notice %s: %r", name, notice.get("info"))
            return
        # Studio's handleProcessingEvent reads ``content``, else ``info``.
        content = notice.get("content") or notice.get("info")
        if not isinstance(content, dict):
            return
        task_id = str(content.get("taskId") or self._latest.get("task_id") or "")
        if content.get("taskId"):
            self._latest["task_id"] = task_id
        kind = _X1_PROCESS_EVENTS.get(str(content.get("action")))
        if kind is not None:
            self._maybe_emit_job_event(
                kind, task_id, {"task_id": task_id} if task_id else None,
            )

    def _dispatch_x1_alarms(self, alarms: Any) -> None:
        """Studio's handleAlarmEvent: ``[{info: {code, message}, level}]``.

        The fire alarm (code 1001) fires the fire warning; the Alarm
        sensor follows the active alarm list, read every sixth poll.
        """
        if not isinstance(alarms, list):
            return
        for alarm in alarms:
            info = alarm.get("info") if isinstance(alarm, dict) else None
            if isinstance(info, dict) and str(info.get("code")) == str(X1_ALARM_FIRE):
                self._latest["alarm_present"] = True
                self._pending_events.append(
                    ("fire_warning", "triggered", {"level": alarm.get("level")}),
                )
                return

    def _dispatch_m2_report(self, url: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            super()._dispatch_m2_report(url, payload)
            return
        if url == M2_PATH_STATE:
            # {mode, moveState, gohomeState, taskId, processInfo, …}
            mode = payload.get("mode")
            if isinstance(mode, str) and mode in X1_MODE_MAP:
                self._latest["status"] = X1_MODE_MAP[mode]
            if payload.get("taskId"):
                self._latest["task_id"] = str(payload["taskId"])
        elif url == X1_PATH_LID:
            # Studio's lid push: ``state`` True = open
            if isinstance(payload.get("state"), bool):
                self._latest["cover_open"] = payload["state"]
        elif url == X1_PATH_USB_KEY:
            if isinstance(payload.get("state"), bool):
                self._latest["machine_lock"] = payload["state"]
        elif url == X1_PATH_FILL_LIGHT:
            # Only the light that changed: {count: 1, fillLights: [...]}
            self._apply_fill_lights(payload)
        elif url == X1_PATH_FEEDER_SENSOR:
            self._apply_feeder_sensors(payload)
        elif url == X1_PATH_ACCESSORY_MESSAGE:
            # {dev_id, message}: an accessory's own report, e.g. the Fire
            # Safety Set 2's M9033 about every 2 s
            if isinstance(payload.get("dev_id"), int) and isinstance(payload.get("message"), str):
                self.pending_accessory_messages.append((payload["dev_id"], payload["message"]))
        else:
            super()._dispatch_m2_report(url, payload)
