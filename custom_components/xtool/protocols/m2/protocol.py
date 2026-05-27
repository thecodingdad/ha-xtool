"""M2 protocol — WS-V2 multi-channel TLS WebSocket with M2-specific URL set.

xTool Studio's `xcs-extension` manifest classifies M2 (JS002) as
``protocolVersion: V2`` / ``channelType: socket``. The M2 firmware
binds the standard V2 framework — TLS WebSocket on port 28900 with
three concurrent channels (instruction / file_stream / media_stream).

What differs from the existing WS-V2 family is the **URL surface**
carried inside the V2 instruction frames:

- ``/v1/platform/device/*`` namespace (machine-info, state, config,
  capabilities, alarm, upgrade) replaces the WS-V2 family's
  ``/v1/device/machine_information`` / ``runtime-infos`` /
  ``configs`` / ``alarms`` / ``statistics``.
- ``/v1/project/*`` namespace (per-peripheral endpoints, per-tool
  endpoints, inkjet, measure, calibration, control) replaces the
  WS-V2 family's ``/v1/peripheral/param`` + ``/v1/cnc/*`` +
  ``/v1/laser-head/*`` umbrellas.
- Job control: POST ``/v1/project/device/control?action=START|PAUSE|
  RESUME|CANCEL`` (not WS-V2's ``/v1/processing/state?action=...``).
- Camera snap: POST ``/v1/platform/camera/snap?name=far|near|side``
  (not WS-V2's GET ``/v1/camera/snap?name=...`` via file_stream).

22 URLs are shared with the existing family — all transport-layer
(BT accessory passthrough via ``/v1/parts/control`` + M9098/M9082/
M9033, file_transfer, OTA upgrade, log packaging). M2 inherits
those by extending ``WSV2Protocol``.
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import DeviceInfo, LaserInfo, XtoolDeviceState
from ..ws_v2.protocol import WSV2Protocol

_LOGGER = logging.getLogger(__name__)


# --- M2 URL constants (instruction-frame routing keys) ----------------------

M2_PATH_MACHINE_INFO = "/v1/platform/device/machine-info"
M2_PATH_MACHINE_INFO_NAME = "/v1/platform/device/machine-info/name"
M2_PATH_STATE = "/v1/platform/device/state"
M2_PATH_STATE_SYNC = "/v1/platform/device/state/sync"
M2_PATH_CONFIG = "/v1/platform/device/config"
M2_PATH_CAPABILITIES = "/v1/platform/device/capabilities"
M2_PATH_ALARM = "/v1/platform/device/alarm"
M2_PATH_CAMERA_SNAP = "/v1/platform/camera/snap"
M2_PATH_CAMERA_LIST = "/v1/platform/camera/list"
M2_PATH_CAMERA_LIVE = "/v1/platform/camera/live"

M2_PATH_DEVICE_CONTROL = "/v1/project/device/control"
M2_PATH_COORDINATE = "/v1/project/device/coordinate"
M2_PATH_CONTROL_HOME = "/v1/project/control/home"
M2_PATH_RUNNING_STATUS = "/v1/project/running/status"
M2_PATH_LASER_HEAD_INFO = "/v1/project/laser-head/info"
M2_PATH_LASER_HEAD_TEMP = "/v1/project/laser-head/get-temp"
M2_PATH_NTC_TEMP = "/v1/project/ntc/temperature"
M2_PATH_PERIPHERAL_LID = "/v1/project/peripheral/lid"
M2_PATH_PERIPHERAL_PALLET = "/v1/project/peripheral/pallet"
M2_PATH_PERIPHERAL_FILL_LIGHT = "/v1/project/peripheral/fill-light"
M2_PATH_PERIPHERAL_AIR_PUMP = "/v1/project/peripheral/airPump-control"
M2_PATH_PERIPHERAL_SMOKE_FAN = "/v1/project/peripheral/smokeFan-control"

# Job-control action labels carried in the ``params`` field.
M2_ACTION_START = "START"
M2_ACTION_PAUSE = "PAUSE"
M2_ACTION_RESUME = "RESUME"
M2_ACTION_CANCEL = "CANCEL"


# Mode-enum mapping from Studio's bundle. Studio's
# ``deviceMessageService`` extracts ``data.mode`` on the
# ``/v1/platform/device/state`` push event and matches against
# this set. M2 reuses the V2 ``P_*`` enum.
from ...const import XtoolStatus  # noqa: E402

M2_MODE_MAP: dict[str, XtoolStatus] = {
    "P_IDLE": XtoolStatus.IDLE,
    "P_FRAMING": XtoolStatus.FRAMING,
    "P_FRAME_READY": XtoolStatus.FRAME_READY,
    "P_PROCESSING_READY": XtoolStatus.PROCESSING_READY,
    "P_PROCESSING": XtoolStatus.PROCESSING,
    "P_PAUSE": XtoolStatus.PAUSED,
    "P_FINISH": XtoolStatus.FINISHED,
    "P_FINISH_PROCESSING": XtoolStatus.FINISHED,
    "P_SLEEP": XtoolStatus.SLEEPING,
    "P_INITIALIZING": XtoolStatus.INITIALIZING,
    "P_ERROR": XtoolStatus.ERROR_LIMIT,
    "P_EMERGENCY_STOP": XtoolStatus.ERROR_LIMIT,
}


class M2WSV2Protocol(WSV2Protocol):
    """WS-V2 protocol with the M2 (JS002) URL surface.

    Inherits from :class:`WSV2Protocol` for the transport,
    multi-channel handling, push-drain pipeline, BT accessory
    subsystem, file_stream and OTA helpers. Overrides only the
    URL-specific methods.
    """

    # --- Device identity ----------------------------------------------------

    async def get_device_info(self) -> DeviceInfo:
        info = DeviceInfo()
        try:
            data = await self.request(M2_PATH_MACHINE_INFO, "GET")
        except Exception:
            data = {}
        _LOGGER.debug("M2 %s raw: %s", M2_PATH_MACHINE_INFO, data)
        if isinstance(data, dict):
            info.device_name = str(
                data.get("deviceName") or data.get("name") or ""
            )
            info.serial_number = str(
                data.get("sn") or data.get("snCode") or ""
            )
            info.mac_address = str(data.get("mac") or "")
            firmware = data.get("firmware") or {}
            if isinstance(firmware, dict):
                info.main_firmware = str(
                    firmware.get("package_version")
                    or firmware.get("version")
                    or ""
                )
            elif isinstance(firmware, str):
                info.main_firmware = firmware
            laser_power = data.get("laserPower")
            if isinstance(laser_power, list) and laser_power:
                first = laser_power[0]
                power = (
                    first.get("power")
                    if isinstance(first, dict)
                    else first
                )
                if isinstance(power, (int, float)) and power:
                    info.laser = LaserInfo(power_watts=int(power))
        # Carry forward any push-cached identity.
        if not info.device_name:
            info.device_name = self._latest.get("device_name", "")
        if not info.serial_number:
            info.serial_number = self._latest.get("serial_number", "")
        if not info.mac_address:
            info.mac_address = self._latest.get("mac_address", "")
        if not info.main_firmware:
            info.main_firmware = self._latest.get("firmware_version", "")
        self._latest["device_name"] = info.device_name
        self._latest["serial_number"] = info.serial_number
        self._latest["firmware_version"] = info.main_firmware
        if info.mac_address:
            self._latest["mac_address"] = info.mac_address
        return info

    # --- Polling ------------------------------------------------------------

    async def poll_state(self, state: XtoolDeviceState) -> None:
        if not self._connected:
            await self.connect()

        # Status — /v1/platform/device/state is a push-event URL key
        # only (DEVICE_STATE NOTIFY frames keyed by it). Actively
        # pulling a snapshot goes via Studio's ``syncDeviceState``
        # → POST /v1/platform/device/state/sync. Response payload
        # is the same shape as the push: ``{curMode:{mode,desc,subMode,taskId}}``.
        try:
            s = await self.request(M2_PATH_STATE_SYNC, method="POST")
        except Exception as err:
            _LOGGER.debug("M2 %s failed: %s", M2_PATH_STATE_SYNC, err)
            s = {}
        if isinstance(s, dict):
            cur = s.get("curMode") or {}
            if isinstance(cur, dict):
                mode = cur.get("mode")
                if isinstance(mode, str):
                    state.status = M2_MODE_MAP.get(mode, XtoolStatus.UNKNOWN)
                task_id = cur.get("taskId")
                if task_id:
                    state.task_id = str(task_id)

        # Cover / lid
        try:
            lid = await self.request(M2_PATH_PERIPHERAL_LID, "GET")
        except Exception:
            lid = {}
        if isinstance(lid, dict):
            lid_state = lid.get("state")
            if isinstance(lid_state, str):
                state.cover_open = lid_state == "on"

        # Coordinate — laser head X/Y/Z position
        try:
            coord = await self.request(M2_PATH_COORDINATE, "GET")
        except Exception:
            coord = {}
        if isinstance(coord, dict):
            for src, dst in (
                ("x", "position_x"),
                ("y", "position_y"),
                ("z", "position_z"),
            ):
                v = coord.get(src)
                if isinstance(v, (int, float)):
                    setattr(state, dst, float(v))

        # Drain push-cached fields the same way WS-V2 does.
        self._apply_latest_to_state(state)

    # --- Job control --------------------------------------------------------

    async def _device_action(self, action: str) -> None:
        await self.request(
            M2_PATH_DEVICE_CONTROL,
            method="POST",
            params={"action": action},
        )

    async def pause_job(self) -> None:
        await self._device_action(M2_ACTION_PAUSE)

    async def resume_job(self) -> None:
        await self._device_action(M2_ACTION_RESUME)

    async def cancel_job(self) -> None:
        await self._device_action(M2_ACTION_CANCEL)

    async def home_all(self) -> None:
        await self.request(
            M2_PATH_CONTROL_HOME, method="POST", params={"axis": "ALL"},
        )

    async def home_z(self) -> None:
        await self.request(
            M2_PATH_CONTROL_HOME, method="POST", params={"axis": "Z"},
        )

    # --- Camera snap --------------------------------------------------------

    async def camera_snap(self, name: str = "far") -> bytes | None:
        """M2 camera-snap differs from WS-V2 base.

        Studio's `cameraSnap` route is ``POST /v1/platform/camera/snap``
        with ``params:{name:"far"|"near"|"side"}``. Response shape
        unverified without test hardware — the M2 firmware likely
        returns image bytes either inline (base64 in the JSON reply)
        or via a follow-up file_stream pull. For v2.5.14 we issue
        the POST so the entity wiring is in place; actual image
        decoding will land once a real device exposes the wire shape.
        """
        try:
            reply = await self.request(
                M2_PATH_CAMERA_SNAP, method="POST", params={"name": name},
            )
        except Exception as err:
            _LOGGER.debug("M2 camera snap %s failed: %s", name, err)
            return None
        _LOGGER.debug("M2 camera snap %s reply: %r", name, reply)
        if isinstance(reply, dict):
            data = reply.get("data") or reply.get("image")
            if isinstance(data, str):
                import base64
                try:
                    return base64.b64decode(data)
                except Exception:
                    return None
        return None

    # --- Firmware update helper --------------------------------------------

    async def get_firmware_versions(self, coordinator: Any) -> dict[str, str]:
        # Single-package cloud check. The on-device firmware bundle
        # splits MR536 / InkjetController / LaserController /
        # MotionController, but the cloud API treats it as one
        # content_id.
        if coordinator.firmware_version:
            return {"main": coordinator.firmware_version}
        return {}
