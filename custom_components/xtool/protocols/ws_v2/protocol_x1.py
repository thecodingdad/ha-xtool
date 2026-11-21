"""WS-V2 protocol dialect for the xTool X1 (JS001).

The X1 is a different machine from the M2: a 55 W CO2 gantry with a
flying galvo head, swappable laser sources, three cameras and an
optional feeder. What it shares with the M2 is xTool's platform 2.0
interface, which Studio drives the same way for both: the ``REPORT``
pushes, ``/v1/platform/device/*`` (machine-info, state/sync, config),
``/v1/project/device/control`` for jobs, the process statistics and
the camera snap. This class takes those from
:class:`M2WSV2Protocol` and replaces everything that belongs to the
M2's hardware.

Verified against Studio 1.11.9's X1 extension (``xcs-ext-js001``
0.2.30) and the replies an X1 owner captured in PR #61.
"""

from __future__ import annotations

import logging
from typing import Any

from ...const import XtoolStatus
from ..base import XtoolDeviceState
from .protocol_m2 import (
    M2_PATH_CONFIG,
    M2_PATH_STATE,
    M2_PATH_STATE_SYNC,
    M2WSV2Protocol,
)

_LOGGER = logging.getLogger(__name__)

X1_PATH_LID_STATUS = "/v1/project/peripheral/lid-status"
X1_PATH_USB_KEY = "/v1/project/peripheral/usbkey"

# Studio reads the settings with the keys it wants; these are the ones
# with an entity.
X1_CONFIG_READ_KEYS = ["smokeFanTimeout"]

# Studio's X1 mode enum. The /state/sync reply is empty; the mode
# arrives in the /v1/platform/device/state push that the sync triggers.
X1_MODE_MAP: dict[str, XtoolStatus] = {
    "Initial": XtoolStatus.INITIALIZING,
    "Idle": XtoolStatus.IDLE,
    "Busy": XtoolStatus.WORKING_API,
    "Measure": XtoolStatus.MEASURING,
    "Leveling": XtoolStatus.MEASURING,
    "Calibration": XtoolStatus.MEASURING,
    "Autowork": XtoolStatus.PROCESSING,
    "SecureConfirm": XtoolStatus.PROCESSING_READY,
    "WorkReady": XtoolStatus.PROCESSING_READY,
    "WorkPlay": XtoolStatus.PROCESSING,
    "WorkPause": XtoolStatus.PAUSED,
    "Sleep": XtoolStatus.SLEEPING,
    "Upgrade": XtoolStatus.FIRMWARE_UPDATE,
    "Network": XtoolStatus.WIFI_SETUP,
    "Error": XtoolStatus.ERROR_LIMIT,
}

# Studio's resumePrint sends RESUME; the M2 resumes with START.
_X1_JOB_ACTIONS: dict[str, str] = {
    "start": "RESUME",
    "pause": "PAUSE",
    "stop": "CANCEL",
}


class X1WSV2Protocol(M2WSV2Protocol):
    """The X1: the platform 2.0 interface without the M2's hardware."""

    _STATS_POLL_EVERY = 6

    def __init__(self, host: str, port: int | None = None) -> None:
        super().__init__(host, port)
        self._x1_poll_counter = 0

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Status, lid and safety key every poll; settings and statistics every sixth."""
        if not self._connected:
            await self.connect()

        try:
            await self.request(M2_PATH_STATE_SYNC, method="POST")
        except Exception as err:
            _LOGGER.debug("X1 %s failed: %s", M2_PATH_STATE_SYNC, err)

        lid = await self._get_dict(X1_PATH_LID_STATUS)
        if isinstance(lid.get("lidStatus"), bool):
            state.cover_open = lid["lidStatus"]
        key = await self._get_dict(X1_PATH_USB_KEY)
        if isinstance(key.get("usbKey"), bool):
            state.machine_lock = key["usbKey"]

        if self._x1_poll_counter % self._STATS_POLL_EVERY == 0:
            config = await self._get_dict(M2_PATH_CONFIG, data={"kv": X1_CONFIG_READ_KEYS})
            self._apply_m2_configs(config)
            await self._poll_m2_statistics(state)
        self._x1_poll_counter += 1

        self._apply_latest_to_state(state)

    async def _get_dict(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            reply = await self.request(url, "GET", **kwargs)
        except Exception as err:
            _LOGGER.debug("X1 %s failed: %s", url, err)
            return {}
        return reply if isinstance(reply, dict) else {}

    async def set_processing_state(self, action: str) -> dict[str, Any]:
        return await self.request(
            "/v1/project/device/control",
            method="POST",
            params={"action": _X1_JOB_ACTIONS[action]},
        )

    async def set_peripheral(
        self, peripheral_type: str, action: str | None = None, **extra: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            f"X1 has no {peripheral_type!r} control in Home Assistant yet"
        )

    def _dispatch_m2_report(self, url: str, payload: Any) -> None:
        if url == M2_PATH_STATE:
            # {mode, moveState, gohomeState, taskId, processInfo}
            if isinstance(payload, dict):
                mode = payload.get("mode")
                if isinstance(mode, str) and mode in X1_MODE_MAP:
                    self._latest["status"] = X1_MODE_MAP[mode]
                task_id = payload.get("taskId")
                if task_id:
                    self._latest["task_id"] = str(task_id)
            return
        if url == X1_PATH_USB_KEY:
            if isinstance(payload, dict) and isinstance(payload.get("state"), bool):
                self._latest["machine_lock"] = payload["state"]
            return
        # The lid push ({state: True = open}), alarms, process events and
        # machine-info have the M2's shape.
        super()._dispatch_m2_report(url, payload)
