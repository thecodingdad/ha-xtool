"""WS-V2 protocol dialect for the xTool Apparel Printer (DT001).

DT001 V2 firmware (`xcs-ext-dt001` bundle) is the inkjet variant —
classified ``protocolVersion:"V2"`` in Studio's manifest and rides
the same TLS WebSocket transport as the F1/F2 family, but the
processing-control surface diverges:

- Job control — three discrete POST routes ``/v1/processing/start``,
  ``/v1/processing/pause`` and ``/v1/processing/stop``. The
  ``/v1/processing/state?action=…`` endpoint is **not** exposed.
- Inkjet peripherals — dedicated ``/v1/peripheral/<type>`` routes
  (``/v1/peripheral/fill_light``, ``/v1/peripheral/ink_bottle``,
  ``/v1/peripheral/heater_temp``, …) for inkjet-specific sensors.
  Generic peripherals (``gap``, ``machine_lock``) still ride the
  shared ``/v1/peripheral/param`` path so the base poll machinery
  covers them.
- Camera — no camera entity on the inkjet head, ``camera_snap``
  always returns ``None``.

Statistics, alarms and device-mode are not exposed by DT001 V2 —
those are absorbed by the per-endpoint unsupported cache in the
base class.
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import XtoolDeviceState
from .protocol import WSV2_MODE_MAP, WSV2Protocol

_LOGGER = logging.getLogger(__name__)


class DT001WSV2Protocol(WSV2Protocol):
    """WS-V2 with the DT001 (Apparel Printer) URL set."""

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
            value = payload.get(src)
            if isinstance(value, str) and value:
                self._cache_state_value(state, dst, value)

    def _apply_water_bottle(
        self, state: XtoolDeviceState, payload: dict[str, Any],
    ) -> None:
        for src, dst in (
            ("clean_water_bottle", "clean_water"),
            ("waste_water_bottle", "waste_water"),
        ):
            value = payload.get(src)
            if isinstance(value, str) and value:
                self._cache_state_value(state, dst, value)

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
        """DT001 has no camera — short-circuit any snap request."""
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
            mode = str(cur_mode.get("mode") or "").upper()
            mapped = WSV2_MODE_MAP.get(mode)
            if mapped is not None:
                state.status = mapped
                self._latest["status"] = mapped
            if cur_mode.get("subMode"):
                state.working_mode = str(cur_mode["subMode"])
            if cur_mode.get("taskId"):
                state.task_id = str(cur_mode["taskId"])

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
