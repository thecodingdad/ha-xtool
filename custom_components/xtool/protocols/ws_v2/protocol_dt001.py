"""WS-V2 protocol dialect for the xTool Apparel Printer (DT001).

DT001 V2 firmware (`xcs-ext-dt001` bundle) is the inkjet variant —
classified ``protocolVersion:"V2"`` in Studio's manifest and rides
the same TLS WebSocket transport as the F1/F2 family, but the
processing-control surface diverges:

- Job control — three discrete POST routes ``/v1/processing/start``,
  ``/v1/processing/pause`` and ``/v1/processing/stop``. The
  ``/v1/processing/state?action=…`` endpoint is **not** exposed.
- Inkjet peripherals — dedicated ``/v1/peripheral/<type>`` routes
  (``/v1/peripheral/fill_light``, ``/v1/peripheral/ink_bottle``, …)
  for inkjet-specific sensors. Oven telemetry uses ``/v1/device/roaster``.
  Generic peripherals (``gap``, ``machine_lock``) still ride the
  shared ``/v1/peripheral/param`` path so the base poll machinery
  covers them.
- Camera — the internal calibration camera has an archive-download
  workflow, but no verified on-demand snapshot or live-view route.
  ``camera_snap`` therefore returns ``None``.

Statistics use ``/v1/device/workingInfo``. Alarms and device-mode
still rely on the shared push handling.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from ...const import XtoolStatus
from ..base import XtoolDeviceState
from .protocol import WSV2_MODE_MAP, WSV2Protocol

_LOGGER = logging.getLogger(__name__)


class DT001WSV2Protocol(WSV2Protocol):
    """WS-V2 with the DT001 (Apparel Printer) URL set."""

    MODE_MAP = {
        **WSV2_MODE_MAP,
        "WORK": XtoolStatus.PROCESSING,
        "P_CLEAN": XtoolStatus.CLEANING,
        # This spelling is emitted by firmware 40.100.028 during nozzle tests.
        "P_CALIBRATIPON": XtoolStatus.CALIBRATING,
    }

    # Job control — three URLs instead of one with ?action=.
    PATH_PROCESSING_START = "/v1/processing/start"
    PATH_PROCESSING_PAUSE = "/v1/processing/pause"
    PATH_PROCESSING_STOP = "/v1/processing/stop"

    _ACTION_TO_PATH: dict[str, str] = {
        "start": PATH_PROCESSING_START,
        "pause": PATH_PROCESSING_PAUSE,
        "stop": PATH_PROCESSING_STOP,
    }

    PATH_INK_BOTTLE = "/v1/peripheral/ink_bottle"
    PATH_WATER_BOTTLE = "/v1/peripheral/water_bottle"
    PATH_ROASTER = "/v1/device/roaster"
    PARTS_LINK = "linkMotion"
    # Studio's G7 array and physical open/close tests agree on these indexes.
    PANEL_FIELDS = {
        "gap_top": (0, "top_cover_open"),
        "gap_left": (1, "left_panel_open"),
        "gap_right": (2, "right_panel_open"),
    }
    PATH_STATISTICS = "/v1/device/workingInfo"
    MAINTENANCE_KEYS = (
        "cleanRemindDay", "fittingRemindDay", "numKnifeCut",
        "worktimeFromLastCleanMatain", "worktimeFromLastFittingChange",
    )
    _UNSUPPORTED_ENDPOINT_CODES = (
        "code -2", "code -3", "code 10", "code 1:", "code 404",
    )

    def _cache_state_value(
        self, state: XtoolDeviceState, attr: str, value: Any,
    ) -> None:
        """Keep polled DT001 fields in both the live state and push cache."""
        setattr(state, attr, value)
        self._latest[attr] = value

    def _apply_ink_bottle(
        self, state: XtoolDeviceState, payload: dict[str, Any],
    ) -> None:
        for src, dst in (
            ("ink_c", "ink_cyan"),
            ("ink_k", "ink_black"),
            ("ink_m", "ink_magenta"),
            ("ink_w", "ink_white"),
            ("ink_y", "ink_yellow"),
        ):
            if src not in payload:
                continue
            value = payload.get(src)
            self._cache_state_value(
                state, dst, value if isinstance(value, str) and value in ("normal", "low", "empty") else None,
            )

    def _apply_water_bottle(
        self, state: XtoolDeviceState, payload: dict[str, Any],
    ) -> None:
        for src, dst in (
            ("clean_water_bottle", "clean_water"),
            ("waste_water_bottle", "waste_water"),
        ):
            if src not in payload:
                continue
            value = payload.get(src)
            allowed = ("normal", "empty") if dst == "clean_water" else ("normal", "full", "detach")
            self._cache_state_value(
                state, dst, value if isinstance(value, str) and value in allowed else None,
            )
        waste = payload.get("waste_water_bottle")
        if "waste_water_bottle" in payload:
            # A removed tank reports "detach". Clear any cached full/normal
            # assertion until the sensor can measure an installed tank again.
            self._cache_state_value(
                state, "waste_tank_full", {"normal": False, "full": True}.get(waste) if isinstance(waste, str) else None,
            )

    def _apply_roaster(self, payload: dict[str, Any]) -> None:
        connected = payload.get("connectState")
        if connected in ("Connected", "Disconnected"):
            self._latest["heater_connected"] = connected == "Connected"
        if connected == "Disconnected":
            # The printer stays on for automatic cleaning even with its oven off.
            for attr in ("heater_temperature", "heating_status", "oven_processing", "oven_feed_active"):
                self._latest[attr] = None
            return
        temperature = payload.get("heaterTemp")
        if type(temperature) in (int, float) and math.isfinite(temperature):
            self._latest["heater_temperature"] = float(temperature)
        heating = payload.get("heatingStatus")
        if type(heating) is int:
            self._latest["heating_status"] = heating

    def _apply_mode(self, mode: dict[str, Any]) -> None:
        raw = str(mode.get("mode") or "").upper()
        sub = str(mode.get("subMode") or "")
        mapped = self.MODE_MAP.get(raw)
        if raw == "WORK":
            mapped = {
                "workReady": XtoolStatus.PROCESSING_READY,
                "workPause": XtoolStatus.PAUSED,
            }.get(sub, XtoolStatus.PROCESSING)
        if mapped is not None:
            self._latest["status"] = mapped
        if "subMode" in mode:
            self._latest["working_mode"] = sub
        if mode.get("taskId"):
            self._latest["task_id"] = str(mode["taskId"])

    def _apply_busy_state(self, payload: dict[str, Any]) -> None:
        busy = payload.get("deviceBusyState")
        if not isinstance(busy, dict):
            return
        # Controller activity, not proof that a belt physically moves.
        flag = busy.get("busyFlag")
        if type(flag) is int and flag >= 0:
            self._latest["oven_processing"] = bool(flag & 262144)
            self._latest["oven_feed_active"] = bool(flag & 32768)

    def _apply_panel_states(
        self, state: XtoolDeviceState, payload: dict[str, Any],
    ) -> None:
        panels = payload.get("gapState")
        if not isinstance(panels, list):
            return
        for index, attr in self.PANEL_FIELDS.values():
            if index >= len(panels):
                continue
            value = panels[index]
            opened = bool(value) if type(value) in (int, bool) and value in (0, 1) else None
            self._cache_state_value(state, attr, opened)

    def _apply_maintenance(self, payload: dict[str, Any]) -> None:
        values = dict(self._latest.get("printer_maintenance", {}))
        for key in (*self.MAINTENANCE_KEYS, "cleanNozzleStandard", "cleanNozzleDeep", "bumpExceptions"):
            value = payload.get(key)
            if (type(value) in (int, float) and math.isfinite(value)
                    and (value >= 0 or value == -1 and key in ("cleanRemindDay", "fittingRemindDay"))):
                values[key] = value
        self._latest["printer_maintenance"] = values
        for elapsed_key, days_key, attr in (
            ("worktimeFromLastCleanMatain", "cleanRemindDay", "wiping_maintenance_due"),
            ("worktimeFromLastFittingChange", "fittingRemindDay", "fitting_replacement_due"),
        ):
            elapsed, days = values.get(elapsed_key), values.get(days_key)
            if days == -1:
                self._latest[attr] = False
            elif elapsed is not None and days is not None and days >= 0:
                self._latest[attr] = elapsed >= days * 86400

    def _apply_statistics(self, stats: dict[str, Any]) -> None:
        super()._apply_statistics(stats)
        count = stats.get("numWorking")
        if type(count) is int:
            self._latest["session_count"] = count
        self._apply_maintenance(stats)

    def _dispatch_push(self, event: dict[str, Any]) -> None:
        super()._dispatch_push(event)
        # DT001 sends periodic snapshots on this route, rather than
        # nesting telemetry in /work/mode MODE_CHANGE.
        data = event.get("data")
        if (isinstance(data, dict) and data.get("module") == "HEAD_COLLISION"
                and data.get("type") == "CLOSE"):
            # CLOSE means collision-strip contact on DT001, not fault cleared.
            # Emit only from a live push. Runtime alarm lists retain old entries.
            self._pending_events.append((
                "error", "print_head_collision", {
                    "raw_type": "CLOSE", "info": data.get("info"),
                },
            ))
        if (event.get("url") == "/work/mode" and isinstance(data, dict)
                and data.get("module") == "STATUS_CONTROLLER" and data.get("type") == "MODE_CHANGE"):
            info = data.get("info")
            if isinstance(info, dict):
                self._apply_mode(info)
        if (event.get("url") == "/print_task/status" and isinstance(data, dict)
                and data.get("module") == "PRINT_TASK" and data.get("type") == "TASK_INFO_REPORT"):
            info = data.get("info")
            if isinstance(info, dict):
                elapsed = info.get("processingTime")
                if type(elapsed) is int and elapsed >= 0:
                    self._latest["task_time"] = elapsed
                if info.get("taskId"):
                    self._latest["task_id"] = str(info["taskId"])
        if isinstance(data, dict) and data.get("module") == "GAP":
            typ, info = data.get("type"), data.get("info")
            # These real-time events carry an empty URL on this firmware.
            # The polling alarm lists remain sticky after panels close.
            if typ in ("OPEN", "CLOSE") and isinstance(info, str):
                for name in info.split(":"):
                    panel = self.PANEL_FIELDS.get(name.strip())
                    if panel is not None:
                        self._latest[panel[1]] = typ == "OPEN"
        if (event.get("url") == "/device/status" and isinstance(data, dict)
                and data.get("module") == "DEVICE_STATUS" and data.get("type") == "INFO"):
            info = data.get("info")
            if isinstance(info, dict):
                state = XtoolDeviceState()
                self._apply_panel_states(state, info)
                self._apply_busy_state(info)
                if isinstance(info.get("inkBottle"), dict):
                    self._apply_ink_bottle(state, info["inkBottle"])
                if isinstance(info.get("waterBottle"), dict):
                    self._apply_water_bottle(state, info["waterBottle"])
                heater = info.get("heater")
                if isinstance(heater, dict):
                    self._apply_roaster(heater)
                ambient = info.get("tempHumidty")
                if isinstance(ambient, dict):
                    for src, dst in (("temp", "ambient_temp"), ("humity", "ambient_humidity")):
                        if type(ambient.get(src)) in (int, float):
                            self._latest[dst] = float(ambient[src])
                mode = info.get("curMode")
                if isinstance(mode, dict):
                    self._apply_mode(mode)

    async def _poll_optional_endpoint(self, path: str) -> dict[str, Any] | None:
        """Fetch an optional DT001 endpoint, caching firmware rejections."""
        if path in self._unsupported_endpoints:
            return None
        try:
            payload = await self.request(path, "GET")
        except RuntimeError as err:
            msg = str(err)
            if any(code in msg for code in self._UNSUPPORTED_ENDPOINT_CODES):
                _LOGGER.debug(
                    "DT001 %s rejected by firmware (%s) — caching as unsupported",
                    path, msg,
                )
                self._unsupported_endpoints.add(path)
            else:
                _LOGGER.debug("DT001 %s failed: %s", path, err)
            return None
        except Exception as err:
            _LOGGER.debug("DT001 %s failed: %s", path, err)
            return None
        return payload if isinstance(payload, dict) else None

    async def set_processing_state(self, action: str) -> dict[str, Any]:
        """Dispatch ``start`` / ``pause`` / ``stop`` to the matching POST URL.

        DT001 V2 firmware splits Studio's ``mdStartPrint`` /
        ``pausePrint`` / ``cancelPrint`` routes into three discrete
        POST endpoints (no params, no body). The action verb maps
        straight to the endpoint suffix.
        """
        path = self._ACTION_TO_PATH.get(action)
        if path is None:
            raise ValueError(f"DT001 unknown processing action: {action}")
        return await self.request(path, "POST")

    async def camera_snap(self, camera_name: str = "") -> bytes | None:
        """No verified live/snapshot API for DT001's calibration camera."""
        return None

    async def _poll_runtime_status(self, state: XtoolDeviceState) -> None:
        """Fetch runtime status plus DT001-specific inkjet telemetry."""
        try:
            rt = await self.request(self.PATH_RUNTIME_INFOS, "GET")
        except Exception as err:
            _LOGGER.debug("DT001 %s failed: %s", self.PATH_RUNTIME_INFOS, err)
            rt = {}
        if not isinstance(rt, dict):
            return

        cur_mode = rt.get("curMode") or {}
        if isinstance(cur_mode, dict):
            self._apply_mode(cur_mode)
            for attr in ("status", "working_mode", "task_id"):
                if attr in self._latest:
                    setattr(state, attr, self._latest[attr])

        self._apply_panel_states(state, rt)
        self._apply_busy_state(rt)

        # DT001 40.100.028 reports degrees C (live value: 38).
        # Other V2 models use milli-degrees; keep this model-specific.
        cpu_temp = rt.get("cpuTemp")
        if isinstance(cpu_temp, (int, float)):
            self._cache_state_value(state, "cpu_temp", int(cpu_temp))
        ambient_humidity = rt.get("humity")
        if isinstance(ambient_humidity, (int, float)):
            self._cache_state_value(
                state, "ambient_humidity", float(ambient_humidity),
            )
        ambient_temp = rt.get("temperature")
        if isinstance(ambient_temp, (int, float)):
            self._cache_state_value(state, "ambient_temp", float(ambient_temp))
        heating_status = rt.get("heatingStatus")
        if isinstance(heating_status, (int, float)):
            self._cache_state_value(state, "heating_status", int(heating_status))
        film_buffer_ready = rt.get("filmBufferSta")
        if isinstance(film_buffer_ready, bool):
            self._cache_state_value(
                state, "film_buffer_ready", film_buffer_ready,
            )
        film_position_ready = rt.get("filmPosStatus")
        if isinstance(film_position_ready, bool):
            self._cache_state_value(
                state, "film_position_ready", film_position_ready,
            )
        powder_loop_running = rt.get("powderLoopRun")
        if isinstance(powder_loop_running, bool):
            self._cache_state_value(
                state, "powder_loop_running", powder_loop_running,
            )

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Refresh base V2 state plus DT001 consumable sensors."""
        await super().poll_state(state)

        ink_bottle = await self._poll_optional_endpoint(self.PATH_INK_BOTTLE)
        if ink_bottle is not None:
            self._apply_ink_bottle(state, ink_bottle)

        water_bottle = await self._poll_optional_endpoint(self.PATH_WATER_BOTTLE)
        if water_bottle is not None:
            self._apply_water_bottle(state, water_bottle)

        roaster = await self._poll_optional_endpoint(self.PATH_ROASTER)
        if roaster is not None:
            self._apply_roaster(roaster)
        if (self._poll_counter - 1) % 12 == 0:
            try:
                config = await self.request(
                    self.PATH_CONFIGS_GET, "GET", data={"kv": list(self.MAINTENANCE_KEYS)},
                )
            except Exception as err:
                _LOGGER.debug("DT001 maintenance config failed: %s", err)
            else:
                if isinstance(config, dict):
                    self._apply_maintenance(config)
        for attr in (
            "heater_connected", "heater_temperature", "heating_status",
            "oven_processing", "oven_feed_active",
            "waste_tank_full", "wiping_maintenance_due", "fitting_replacement_due",
            "printer_maintenance",
        ):
            if attr in self._latest:
                setattr(state, attr, self._latest[attr])
