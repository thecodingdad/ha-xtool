"""WS-V2 protocol dialect for the xTool O1 Omni Printer (DT002).

The O1 Omni (UV / DTG printer, Studio ``deviceCode`` ``DT002``,
``machineType`` ``PRINT``) is a V2-only device. It rides the same TLS
WebSocket framework on port 28900 as the rest of the WS-V2 family
(instruction channel, CRC-wrapped JSON frames, ``/v1/user/parity``
handshake), and uses the ``/v1/platform/*`` + ``/v1/project/*`` route
families that the M2 introduced — but the URL set is printer-specific.

Everything below was verified against a live O1 (firmware
``40.101.013.01.ht1``, UV & DT edition) and Studio's DT002 extension
bundle v0.6.8.

Read routes polled (all GET on the instruction channel):

- ``/v1/platform/device/machine-info`` — identity + per-board firmware.
- ``/v1/platform/wifi/connected-info`` — ``mac_addr`` for the device
  registry connection.
- ``/v1/project/device/peripheral-status`` — ``doors[{type,status}]``
  (type 1 = front, 2 = rear; ``status`` True = open), ``lid`` (True =
  open), ``filter``, ``pallet``.
- ``/v1/project/device/ink-tank`` — ``inkRoad1[]`` / ``inkRoad2[]``,
  one entry per cartridge slot: ``exist``, ``remain_ml``, ``full_ml``,
  ``level`` (1 = insufficient), ``serial``. The colour is encoded in
  ``serial[6:8]`` (``"UC"`` = UV Cyan, ``"DK"`` = DTG Black, …).
- ``/v1/project/device/maintenance-tank`` — ``moisturizing[]`` (same
  shape as ink) and ``waste{exist, level}`` (``level`` 2 = full).
- ``/v1/project/device/temp-humity`` — ``{temp, humity}`` (sic).
- ``/v1/project/device/printer-head-param`` — per-head ``temp``.
- ``/v1/project/task/list`` — ``print_tasks[]`` / ``history_tasks[]``.
- ``/v1/project/device/config/all`` — lifetime counters. NOTE: the
  reply's ``data`` is a JSON-encoded *string*, not an object.

Not available on DT002 (firmware answers 404): ``/v1/project/process/
statistics``, a GET on ``/v1/platform/device/alarm``, and the legacy
``/v1/peripheral/maintenanceTank``. There is no state-snapshot route
either; the firmware pushes ``/v1/platform/device/state`` right after
the parity handshake and on every change.

Push envelopes (three shapes, all ``type:"request"``):

- ``method:"REPORT"`` — ``data`` is ``{"<url>": <payload>, …}``.
- ``method:"NOTIFY"`` — ``data`` is ``[{name:"<url>", info, level,
  timestamp}, …]``.
- ``method:"NOTIFY-RECORD"`` — ``data`` is ``[{name:"<url>", content,
  timestamp}, …]`` (job process actions).

During a print the firmware pauses on its own every few minutes for a
short head clean (``WorkPause`` → ``Clean`` → ``WorkPause`` →
``WorkPlay`` within ~4 s). ``Clean`` maps to its own
:attr:`XtoolStatus.CLEANING` so those maintenance pauses do not read as
user pauses.

This first iteration is read-only: no config writes, job control or
firmware flashing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ...const import XtoolStatus
from ..base import DeviceInfo, XtoolDeviceState
from .protocol import WSV2Protocol

_LOGGER = logging.getLogger(__name__)


# --- URL routing keys -------------------------------------------------------

DT002_PATH_MACHINE_INFO = "/v1/platform/device/machine-info"
DT002_PATH_WIFI_INFO = "/v1/platform/wifi/connected-info"
DT002_PATH_PERIPHERAL_STATUS = "/v1/project/device/peripheral-status"
DT002_PATH_INK_TANK = "/v1/project/device/ink-tank"
DT002_PATH_MAINTENANCE_TANK = "/v1/project/device/maintenance-tank"
DT002_PATH_TEMP_HUMIDITY = "/v1/project/device/temp-humity"
DT002_PATH_PRINTER_HEAD = "/v1/project/device/printer-head-param"
DT002_PATH_TASK_LIST = "/v1/project/task/list"
DT002_PATH_CONFIG_ALL = "/v1/project/device/config/all"

# Push routing keys.
DT002_PUSH_STATE = "/v1/platform/device/state"
DT002_PUSH_ALARM = "/v1/platform/device/alarm"
DT002_PUSH_PROCESS = "/v1/platform/device/process"
DT002_PUSH_DEVICE_INFO = "/v1/project/device/info"
DT002_PUSH_FRONT_DOOR = "/v1/project/peripheral/front_door"
DT002_PUSH_REAR_DOOR = "/v1/project/peripheral/rear_door"
DT002_PUSH_LID = "/v1/project/peripheral/lid"
DT002_PUSH_WASTE_TANK = "/v1/project/peripheral/waste_tank_status"
DT002_PUSH_INK_TANK = "/v1/project/peripheral/ink_tank_status"
DT002_PUSH_MOISTURE_TANK = "/v1/project/peripheral/moisture_tank_status"
DT002_PUSH_TEMP_HUMIDITY = "/v1/project/peripheral/temp_humity"
DT002_PUSH_TASK_UPDATE = "/v1/project/task/printing-task-update"
DT002_PUSH_TASK_LIST_UPDATE = "/v1/project/task/task-list-update"


# Firmware state-machine names (Studio DT002 bundle enum) → integration
# status. ``Motion`` is a head move outside a job; ``Scan`` /
# ``Calibration`` / ``Measure`` are the CIS-scanner and height-probe
# phases; ``WorkDrying`` / ``WorkBaking`` / ``Cutting`` / ``KnifeCut``
# are in-job phases of the DTF / roll workflows.
DT002_MODE_MAP: dict[str, XtoolStatus] = {
    "Initial":       XtoolStatus.INITIALIZING,
    "Idle":          XtoolStatus.IDLE,
    "Clean":         XtoolStatus.CLEANING,
    "Scan":          XtoolStatus.MEASURING,
    "Motion":        XtoolStatus.IDLE,
    "Calibration":   XtoolStatus.MEASURING,
    "Measure":       XtoolStatus.MEASURING,
    "WorkReady":     XtoolStatus.PROCESSING_READY,
    "WorkPlay":      XtoolStatus.PROCESSING,
    "WorkPause":     XtoolStatus.PAUSED,
    "WorkDrying":    XtoolStatus.PROCESSING,
    "WorkRollPause": XtoolStatus.PAUSED,
    "WorkBaking":    XtoolStatus.PROCESSING,
    "KnifeCut":      XtoolStatus.PROCESSING,
    "Cutting":       XtoolStatus.PROCESSING,
    "Sleep":         XtoolStatus.SLEEPING,
    "LowPower":      XtoolStatus.SLEEPING,
    "Upgrade":       XtoolStatus.FIRMWARE_UPDATE,
    "Network":       XtoolStatus.WIFI_SETUP,
    # Details of an error live in the alarm list; the status enum's
    # ERROR_* members are laser-specific, so stay neutral here.
    "Error":         XtoolStatus.UNKNOWN,
}

# Process-push actions that map straight to Job events. ``pause`` /
# ``resume`` are handled separately: the firmware pauses on its own for
# a head clean every few minutes mid-print (``pause`` push, then a
# ``/v1/project/clean/start`` notify ~30 ms later), so a pause is only
# reported once no clean has started within the grace window.
_PROCESS_ACTION_EVENTS: dict[str, str] = {
    "start": "started",
    "finish": "finished",
    "cancel": "cancelled",
}
_AUTO_CLEAN_GRACE_SECONDS = 2.0
DT002_PUSH_CLEAN_START = "/v1/project/clean/start"

# Cartridge colour codes: ``serial[6:8]`` = <ink type><colour>.
_INK_TYPE_NAMES: dict[str, str] = {"U": "UV", "D": "DTG"}
_INK_COLOUR_NAMES: dict[str, str] = {
    "C": "Cyan",
    "M": "Magenta",
    "Y": "Yellow",
    "K": "Black",
    "W": "White",
    "S": "White",  # DTG white cartridge (290 ml) reports "S"
    "V": "Varnish",
    "F": "Moisturizing fluid",
}

# Slow-cadence divisors on the base 5 s poll interval.
_CONSUMABLES_POLL_EVERY = 6   # ~30 s: ink, maintenance tank, env, heads, queue
_COUNTERS_POLL_EVERY = 60     # ~5 min: lifetime counters from config/all


def cartridge_name(serial: str, road: int, slot: int) -> str:
    """Human name for a cartridge slot, from its serial's colour code."""
    code = serial[6:8] if isinstance(serial, str) and len(serial) >= 8 else ""
    ink_type = _INK_TYPE_NAMES.get(code[:1], "")
    colour = _INK_COLOUR_NAMES.get(code[1:2], "")
    if ink_type and colour:
        return f"{ink_type} {colour}"
    return f"Ink path {road} slot {slot}"


def cartridge_percent(remain_ml: Any, full_ml: Any) -> float | None:
    """Remaining percentage, clamped to 0-100 (Studio's ``gS`` helper)."""
    if not isinstance(remain_ml, (int, float)) or not isinstance(full_ml, (int, float)):
        return None
    if full_ml <= 0 or remain_ml < 0:
        return None
    return round(max(0.0, min(100.0, remain_ml / full_ml * 100.0)), 1)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class DT002WSV2Protocol(WSV2Protocol):
    """WS-V2 protocol with the O1 Omni (DT002) URL surface."""

    PATH_DEVICE_INFO = DT002_PATH_MACHINE_INFO

    # Fields this dialect adds to the base push-drain list.
    _LATEST_STATE_FIELDS = WSV2Protocol._LATEST_STATE_FIELDS + (
        "front_door_open", "rear_door_open", "filter_installed",
        "waste_tank_present", "waste_tank_full",
        "print_head_temperatures", "active_alarms",
        "job_name", "job_progress", "job_time_remaining",
        "queued_jobs", "air_purifier_runtime_seconds",
        "uv_lamp_runtime_seconds",
    )

    def __init__(self, host: str, port: int | None = None) -> None:
        if port is None:
            super().__init__(host)
        else:
            super().__init__(host, port)
        self._dt002_poll_counter = 0
        # Per-cartridge / moisturizing state, keyed "<road>_<slot>" and
        # "<index>" (1-based). Mutable dicts merged into the state copy
        # on every drain so pushes and polls compose.
        self._cartridges: dict[str, dict[str, Any]] = {}
        self._moisturizing: dict[str, dict[str, Any]] = {}
        # Active print task snapshot (from task/list + progress pushes).
        self._active_task: dict[str, Any] = {}
        # Pause-event debounce (see ``_PROCESS_ACTION_EVENTS``).
        self._pending_pause: asyncio.TimerHandle | None = None
        self._user_paused = False

    # --- Push-cache drain -----------------------------------------------

    def _apply_latest_to_state(self, state: XtoolDeviceState) -> None:
        super()._apply_latest_to_state(state)
        if self._cartridges:
            state.ink_cartridges = {k: dict(v) for k, v in self._cartridges.items()}
        if self._moisturizing:
            state.moisturizing_tanks = {
                k: dict(v) for k, v in self._moisturizing.items()
            }

    # --- Identity ---------------------------------------------------------

    async def get_device_info(self) -> DeviceInfo:
        """Identity from ``machine-info``; MAC from ``wifi/connected-info``."""
        info = DeviceInfo()
        try:
            data = await self.request(DT002_PATH_MACHINE_INFO, "GET")
        except Exception as err:
            _LOGGER.debug("DT002 %s failed: %s", DT002_PATH_MACHINE_INFO, err)
            data = {}
        if isinstance(data, dict):
            info.device_name = str(data.get("deviceName") or "")
            info.serial_number = str(data.get("sn") or "")
            firmware = data.get("firmware")
            if isinstance(firmware, dict):
                info.main_firmware = str(firmware.get("version") or "")
                modules = firmware.get("modules")
                if isinstance(modules, list):
                    for mod in modules:
                        if isinstance(mod, dict) and mod.get("name") == "MR536":
                            hard = mod.get("hardVersion")
                            if hard:
                                info.hardware_version = str(hard)
        try:
            wifi = await self.request(DT002_PATH_WIFI_INFO, "GET")
        except Exception as err:
            _LOGGER.debug("DT002 %s failed: %s", DT002_PATH_WIFI_INFO, err)
            wifi = {}
        if isinstance(wifi, dict) and wifi.get("mac_addr"):
            info.mac_address = str(wifi["mac_addr"])

        for attr, key in (
            ("device_name", "device_name"),
            ("serial_number", "serial_number"),
            ("main_firmware", "firmware_version"),
            ("mac_address", "mac_address"),
        ):
            value = getattr(info, attr)
            if value:
                self._latest[key] = value
            else:
                setattr(info, attr, self._latest.get(key, ""))
        return info

    async def get_firmware_versions(self, coordinator: Any) -> dict[str, str]:
        """Single cloud package (``xTool-dt002-firmware``)."""
        if coordinator.firmware_version:
            return {"main": coordinator.firmware_version}
        return {}

    async def flash_firmware(self, *args: Any, **kwargs: Any) -> None:
        """Not supported yet — install O1 firmware from xTool Studio."""
        raise NotImplementedError(
            "Firmware installation is not supported for the xTool O1 "
            "Omni yet. Install updates from xTool Studio."
        )

    # --- Polling ----------------------------------------------------------

    async def _get(self, url: str) -> Any:
        """GET with the per-connection unsupported-endpoint cache."""
        if url in self._unsupported_endpoints:
            return None
        try:
            return await self.request(url, "GET")
        except RuntimeError as err:
            if "code 404" in str(err):
                self._unsupported_endpoints.add(url)
            _LOGGER.debug("DT002 %s rejected: %s", url, err)
        except Exception as err:
            _LOGGER.debug("DT002 %s failed: %s", url, err)
        return None

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """O1 polling cycle.

        Status, doors, tanks and alarms all arrive by push; the poll is
        a safety net. Peripherals every tick, consumables / queue every
        ~30 s, lifetime counters every ~5 min.
        """
        if not self._connected:
            await self.connect()

        tick = self._dt002_poll_counter
        self._dt002_poll_counter += 1

        peripherals = await self._get(DT002_PATH_PERIPHERAL_STATUS)
        if isinstance(peripherals, dict):
            self._apply_peripheral_status(peripherals)

        if tick % _CONSUMABLES_POLL_EVERY == 0:
            ink = await self._get(DT002_PATH_INK_TANK)
            if isinstance(ink, dict):
                self._apply_ink_tank(ink)
            maint = await self._get(DT002_PATH_MAINTENANCE_TANK)
            if isinstance(maint, dict):
                self._apply_maintenance_tank(maint)
            env = await self._get(DT002_PATH_TEMP_HUMIDITY)
            if isinstance(env, dict):
                self._apply_temp_humidity(env)
            heads = await self._get(DT002_PATH_PRINTER_HEAD)
            if isinstance(heads, dict):
                self._apply_printer_heads(heads.get("printer_head_param"))
            tasks = await self._get(DT002_PATH_TASK_LIST)
            if isinstance(tasks, dict):
                self._apply_task_list(tasks)

        if tick % _COUNTERS_POLL_EVERY == 0:
            cfg = await self._get(DT002_PATH_CONFIG_ALL)
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except ValueError:
                    cfg = None
            if isinstance(cfg, dict):
                self._apply_counters(cfg)

        self._apply_latest_to_state(state)

    # --- Parsers (shared by poll replies and pushes) ---------------------

    def _apply_peripheral_status(self, data: dict[str, Any]) -> None:
        doors = data.get("doors")
        if isinstance(doors, list):
            for door in doors:
                if not isinstance(door, dict):
                    continue
                is_open = door.get("status")
                if not isinstance(is_open, bool):
                    continue
                if door.get("type") == 1:
                    self._latest["front_door_open"] = is_open
                elif door.get("type") == 2:
                    self._latest["rear_door_open"] = is_open
        lid = data.get("lid")
        if isinstance(lid, bool):
            self._latest["cover_open"] = lid
        filt = data.get("filter")
        if isinstance(filt, bool):
            self._latest["filter_installed"] = filt

    def _update_cartridge(
        self, store: dict[str, dict[str, Any]], key: str, road: int,
        slot: int, entry: dict[str, Any],
    ) -> None:
        """Merge one tank entry (poll or push shape) into ``store``."""
        cur = store.setdefault(key, {"road": road, "slot": slot})
        serial = entry.get("serial")
        if isinstance(serial, str) and serial:
            cur["serial"] = serial
        if isinstance(entry.get("name"), str) and entry["name"]:
            cur["name"] = entry["name"]
        elif "name" not in cur:
            cur["name"] = cartridge_name(cur.get("serial", ""), road, slot)
        if isinstance(entry.get("exist"), bool):
            cur["exist"] = entry["exist"]
        for field_name in ("remain_ml", "full_ml"):
            value = _num(entry.get(field_name))
            if value is not None:
                cur[field_name] = value if value >= 0 else None
        level = entry.get("level")
        if isinstance(level, int) and not isinstance(level, bool):
            cur["low"] = level == 1
        cur["percent"] = (
            cartridge_percent(cur.get("remain_ml"), cur.get("full_ml"))
            if cur.get("exist", True) else None
        )

    def _apply_ink_tank(self, data: dict[str, Any]) -> None:
        for road, road_key in ((1, "inkRoad1"), (2, "inkRoad2")):
            entries = data.get(road_key)
            if not isinstance(entries, list):
                continue
            for slot, entry in enumerate(entries, start=1):
                if isinstance(entry, dict):
                    self._update_cartridge(
                        self._cartridges, f"{road}_{slot}", road, slot, entry,
                    )

    def _apply_maintenance_tank(self, data: dict[str, Any]) -> None:
        moist = data.get("moisturizing")
        if isinstance(moist, list):
            for idx, entry in enumerate(moist, start=1):
                if isinstance(entry, dict):
                    self._update_cartridge(
                        self._moisturizing, str(idx), idx, 1, entry,
                    )
                    self._moisturizing[str(idx)]["name"] = (
                        f"Moisturizing fluid {idx}"
                    )
        waste = data.get("waste")
        if isinstance(waste, dict):
            self._apply_waste(waste)

    def _apply_waste(self, waste: dict[str, Any]) -> None:
        if isinstance(waste.get("exist"), bool):
            self._latest["waste_tank_present"] = waste["exist"]
        level = waste.get("level")
        if isinstance(level, int) and not isinstance(level, bool):
            self._latest["waste_tank_full"] = level == 2

    def _apply_temp_humidity(self, data: dict[str, Any]) -> None:
        temp = _num(data.get("temp"))
        if temp is not None:
            self._latest["ambient_temp"] = round(temp, 1)
        hum = _num(data.get("humity", data.get("humidity")))
        if hum is not None:
            self._latest["ambient_humidity"] = round(hum, 1)

    def _apply_printer_heads(self, heads: Any) -> None:
        if not isinstance(heads, list):
            return
        temps: dict[int, float] = dict(
            self._latest.get("print_head_temperatures") or {}
        )
        for head in heads:
            if not isinstance(head, dict):
                continue
            road = head.get("ink_road")
            temp = _num(head.get("temp"))
            if isinstance(road, int) and temp is not None:
                temps[road] = round(temp, 1)
        self._latest["print_head_temperatures"] = temps

    def _apply_task_list(self, data: dict[str, Any]) -> None:
        tasks = data.get("print_tasks")
        if isinstance(tasks, list):
            self._latest["queued_jobs"] = len(tasks)
            active = next(
                (t for t in tasks if isinstance(t, dict)
                 and t.get("status") in ("PRINTING", "PAUSED", "RIP_PROCESSING")),
                None,
            )
            if active is None and tasks and isinstance(tasks[0], dict):
                active = tasks[0]
            if active:
                self._active_task = dict(active)
                self._publish_task()
            else:
                self._clear_task()
        history = data.get("history_tasks")
        if isinstance(history, list):
            finished = [
                t for t in history
                if isinstance(t, dict) and t.get("status") == "FINISHED"
            ]
            if finished:
                last = max(finished, key=lambda t: t.get("updatedAt") or 0)
                seconds = last.get("processingTime")
                if isinstance(seconds, int):
                    self._latest["last_job_time_seconds"] = seconds

    def _publish_task(self) -> None:
        task = self._active_task
        if task.get("id"):
            self._latest["task_id"] = str(task["id"])
        if isinstance(task.get("name"), str):
            self._latest["job_name"] = task["name"]
        done = task.get("currentPrintPass")
        total = task.get("totalPrintPass")
        if isinstance(done, int) and isinstance(total, int) and total > 0:
            self._latest["job_progress"] = round(
                max(0.0, min(100.0, done / total * 100.0)), 1
            )
        elapsed = task.get("processingTime")
        if isinstance(elapsed, int):
            self._latest["task_time"] = elapsed
            estimate = task.get("estimateTime")
            if isinstance(estimate, int):
                self._latest["job_time_remaining"] = max(0, estimate - elapsed)

    def _clear_task(self) -> None:
        self._active_task = {}
        self._latest["job_name"] = None
        self._latest["job_progress"] = None
        self._latest["job_time_remaining"] = None

    def _apply_counters(self, cfg: dict[str, Any]) -> None:
        for src, dst in (
            ("processTotalTime", "working_seconds"),
            ("standbyTime", "standby_seconds"),
            ("processCount", "session_count"),
            ("uvLampRunTime", "uv_lamp_runtime_seconds"),
            ("airPurifierRunTime", "air_purifier_runtime_seconds"),
        ):
            value = cfg.get(src)
            if isinstance(value, int) and not isinstance(value, bool):
                self._latest[dst] = value
        if isinstance(cfg.get("beepEnable"), bool):
            self._latest["beep_enabled_v2"] = cfg["beepEnable"]

    def _apply_alarms(self, alarms: Any) -> None:
        if not isinstance(alarms, list):
            return
        active: list[dict[str, Any]] = []
        for alarm in alarms:
            if not isinstance(alarm, dict):
                continue
            info = alarm.get("info") if isinstance(alarm.get("info"), dict) else {}
            active.append({
                "code": info.get("code"),
                "level": alarm.get("level"),
                "message": info.get("message") or "",
            })
        self._latest["active_alarms"] = active
        self._latest["alarm_present"] = bool(active)

    # --- Push dispatch ----------------------------------------------------

    def _dispatch_push(self, event: dict[str, Any]) -> None:
        method = event.get("method")
        data = event.get("data")
        if method == "REPORT" and isinstance(data, dict):
            for url, payload in data.items():
                self._dispatch_report(url, payload)
            return
        if method in ("NOTIFY", "NOTIFY-RECORD") and isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._dispatch_notify(item)
            return
        super()._dispatch_push(event)

    def _dispatch_report(self, url: str, payload: Any) -> None:
        if url == DT002_PUSH_STATE:
            if isinstance(payload, dict):
                mode = payload.get("mode")
                if isinstance(mode, str):
                    if mode == "Clean":
                        self._cancel_pending_pause()
                    status = DT002_MODE_MAP.get(mode)
                    if status is None:
                        _LOGGER.debug("DT002 unknown mode %r", mode)
                        status = XtoolStatus.UNKNOWN
                    self._latest["status"] = status
            return
        if url == DT002_PUSH_ALARM:
            self._apply_alarms(payload)
            return
        if not isinstance(payload, dict):
            _LOGGER.debug("DT002 REPORT %s non-dict payload %r", url, payload)
            return
        if url == DT002_PUSH_FRONT_DOOR:
            if isinstance(payload.get("state"), bool):
                self._latest["front_door_open"] = payload["state"]
        elif url == DT002_PUSH_REAR_DOOR:
            if isinstance(payload.get("state"), bool):
                self._latest["rear_door_open"] = payload["state"]
        elif url == DT002_PUSH_LID:
            if isinstance(payload.get("state"), bool):
                self._latest["cover_open"] = payload["state"]
        elif url == DT002_PUSH_WASTE_TANK:
            self._apply_waste(payload)
        elif url == DT002_PUSH_INK_TANK:
            route = payload.get("route")
            tank = payload.get("tank_id")
            if isinstance(route, int) and isinstance(tank, int):
                road, slot = route + 1, tank + 1
                self._update_cartridge(
                    self._cartridges, f"{road}_{slot}", road, slot, payload,
                )
        elif url == DT002_PUSH_MOISTURE_TANK:
            index = payload.get("index")
            if isinstance(index, int):
                key = str(index + 1)
                self._update_cartridge(
                    self._moisturizing, key, index + 1, 1, payload,
                )
                self._moisturizing[key]["name"] = f"Moisturizing fluid {key}"
        elif url == DT002_PUSH_TEMP_HUMIDITY:
            self._apply_temp_humidity(payload)
        elif url == DT002_PUSH_DEVICE_INFO:
            # Periodic full snapshot: ink + maintenance + env + heads.
            tanks = payload.get("ink_tanks")
            if isinstance(tanks, dict):
                self._apply_ink_tank(tanks)
            self._apply_maintenance_tank(payload)
            self._apply_temp_humidity(payload)
            self._apply_printer_heads(payload.get("printer_head_param"))
            runtime = payload.get("airPurifierRunTime")
            if isinstance(runtime, int):
                self._latest["air_purifier_runtime_seconds"] = runtime
        elif url == "/v1/project/peripheral/air_purifier_run_time":
            runtime = payload.get("airPurifierRunTime")
            if isinstance(runtime, int):
                self._latest["air_purifier_runtime_seconds"] = runtime
        else:
            _LOGGER.debug("DT002 REPORT unhandled url=%s payload=%r", url, payload)

    def _dispatch_notify(self, item: dict[str, Any]) -> None:
        url = item.get("name")
        if url == DT002_PUSH_TASK_UPDATE:
            info = item.get("info")
            if not isinstance(info, dict):
                return
            if info.get("id") and info["id"] != self._active_task.get("id"):
                self._active_task = {"id": info["id"]}
            self._active_task.update(
                {k: v for k, v in info.items() if v is not None}
            )
            if info.get("status") in ("FINISHED", "CANCELED", "FAILED"):
                self._latest["queued_jobs"] = max(
                    0, int(self._latest.get("queued_jobs") or 1) - 1,
                )
                elapsed = self._active_task.get("processingTime")
                if info.get("status") == "FINISHED" and isinstance(elapsed, int):
                    self._latest["last_job_time_seconds"] = elapsed
                self._clear_task()
                return
            self._publish_task()
            return
        if url == DT002_PUSH_PROCESS:
            content = item.get("content")
            if not isinstance(content, dict):
                return
            task_id = str(content.get("taskId") or "")
            if task_id:
                self._latest["task_id"] = task_id
            action = str(content.get("action") or "")
            if action == "pause":
                self._schedule_pause_event(task_id)
                return
            if action == "resume":
                self._cancel_pending_pause()
                if self._user_paused:
                    self._user_paused = False
                    self._maybe_emit_job_event(
                        "resumed", task_id, {"task_id": task_id} if task_id else None,
                    )
                return
            kind = _PROCESS_ACTION_EVENTS.get(action)
            if kind:
                self._cancel_pending_pause()
                self._user_paused = False
                attrs: dict[str, Any] = {"task_id": task_id} if task_id else {}
                if kind == "finished":
                    elapsed = self._active_task.get("processingTime")
                    if isinstance(elapsed, int):
                        attrs["duration"] = elapsed
                self._maybe_emit_job_event(kind, task_id, attrs or None)
            return
        if url == DT002_PUSH_CLEAN_START:
            # Automatic mid-print head clean — swallow the pause before it.
            self._cancel_pending_pause()
            return
        if url == DT002_PUSH_TASK_LIST_UPDATE:
            # Queue changed — force the next poll to refresh it.
            self._dt002_poll_counter = 0
            return
        _LOGGER.debug("DT002 NOTIFY %s: %r", url, item)

    # --- Pause debounce ---------------------------------------------------

    def _schedule_pause_event(self, task_id: str) -> None:
        self._cancel_pending_pause()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending_pause = loop.call_later(
            _AUTO_CLEAN_GRACE_SECONDS, self._emit_user_pause, task_id,
        )

    def _cancel_pending_pause(self) -> None:
        if self._pending_pause is not None:
            self._pending_pause.cancel()
            self._pending_pause = None

    def _emit_user_pause(self, task_id: str) -> None:
        self._pending_pause = None
        self._user_paused = True
        self._maybe_emit_job_event(
            "paused", task_id, {"task_id": task_id} if task_id else None,
        )

    async def disconnect(self) -> None:
        self._cancel_pending_pause()
        await super().disconnect()
