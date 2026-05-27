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

from .protocol import WSV2Protocol

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
