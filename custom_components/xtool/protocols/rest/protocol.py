"""REST API protocol for xTool F1/F1Ultra/P2/P2S/M1/M1Ultra/P1/GS models."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from collections.abc import Callable

from ...const import BRIGHTNESS_DEVICE_MAX, BRIGHTNESS_HA_MAX, XtoolStatus
from ..base import (
    DeviceInfo,
    FirmwareFile,
    LaserInfo,
    XtoolDeviceState,
    XtoolProtocol,
)


# --- REST-owned ports -------------------------------------------------------

REST_HTTP_PORT = 8080  # main HTTP API
REST_FIRMWARE_PORT = 8087  # /upgrade_version + /package handshake/upload
REST_CAMERA_PORT = 8329  # /camera/snap, /camera/exposure, /camera/fireRecord

# --- REST API peripheral endpoints (port 8080) -----------------------------

REST_PATH_GAP = "/peripheral/gap"
REST_PATH_AIRASSIST = "/peripheral/airassist"
REST_PATH_DIGITAL_LOCK = "/peripheral/digital_lock"
REST_PATH_FILL_LIGHT = "/peripheral/fill_light"
REST_PATH_IR_LED = "/peripheral/ir_led"
REST_PATH_LASER_HEAD = "/peripheral/laser_head"
REST_PATH_IR_DISTANCE = "/peripheral/ir_measure_distance"
REST_PATH_MODE_SWITCH = "/device/modeSwitch"

# --- REST camera endpoints (port 8329) --------------------------------------

REST_PATH_CAMERA_SNAP = "/camera/snap"
REST_PATH_CAMERA_EXPOSURE = "/camera/exposure"
REST_PATH_CAMERA_FIRE_RECORD = "/camera/fireRecord"

# --- Firmware upload endpoint (single-blob multipart, port 8087) ------------

HTTP_PATH_UPGRADE = "/upgrade"

# --- REST stream + LED indexes ---------------------------------------------

CAMERA_STREAM_OVERVIEW = 0
CAMERA_STREAM_CLOSEUP = 1
IR_LED_INDEX_CLOSEUP = 1
IR_LED_INDEX_GLOBAL = 2

# --- REST peripheral action verbs -------------------------------------------

PERIPHERAL_ACTION_SET_BRIGHTNESS = "set_bri"
PERIPHERAL_ACTION_GET_COORD = "get_coord"
PERIPHERAL_ACTION_GO_TO = "go_to"
PERIPHERAL_ACTION_GET_DISTANCE = "get_distance"
PERIPHERAL_ACTION_ON = "on"
PERIPHERAL_ACTION_OFF = "off"

# REST brightness scale (0–255 native, no conversion needed)
REST_BRIGHTNESS_MAX = 255

# Camera exposure scale (P2/P2S/F1 series)
CAMERA_EXPOSURE_MIN = 0
CAMERA_EXPOSURE_MAX = 255

_LOGGER = logging.getLogger(__name__)


class RestProtocol(XtoolProtocol):
    """REST API protocol used by F1, F1Ultra, P2, P2S, M1, M1Ultra, P1, GS models."""

    def __init__(self, host: str, port: int = REST_HTTP_PORT) -> None:
        """Initialize the protocol."""
        super().__init__(host)
        self._port = port
        self._base_url = f"http://{host}:{port}"

    @property
    def connected(self) -> bool:
        """REST is stateless — always 'connected'."""
        return True

    async def connect(self) -> None:
        """No persistent connection needed for REST."""

    async def disconnect(self) -> None:
        """No persistent connection to close."""

    async def get_version(self) -> str | None:
        """Get firmware version via /system endpoint."""
        data = await self._get_json("/system")
        if data and "firmware" in data:
            return data["firmware"]
        if data and "version" in data:
            return data["version"]
        return None

    async def get_device_info(self) -> DeviceInfo:
        """Get full device info via /device/machineInfo."""
        data = await self._get_json("/device/machineInfo")
        if not data:
            return DeviceInfo()

        # machineInfo returns: deviceName, mac, ip, sn, machineType, machineSubType,
        # laserPower, firmware, etc.
        power = 0
        try:
            power = int(data.get("laserPower", 0))
        except (ValueError, TypeError):
            pass

        firmware_str = ""
        firmware = data.get("firmware")
        if isinstance(firmware, list) and firmware:
            parts = [f.get("version", "") for f in firmware if f.get("version")]
            firmware_str = parts[0] if parts else ""
        elif isinstance(firmware, str):
            firmware_str = firmware

        return DeviceInfo(
            serial_number=data.get("sn", ""),
            device_name=data.get("deviceName", ""),
            laser=LaserInfo(power_watts=power),
            main_firmware=firmware_str,
            mac_address=str(data.get("mac", "") or ""),
        )

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Poll all device state values and populate the state object."""
        # Get status from /cnc/status or /device/runningStatus
        status_data = await self._get_json("/cnc/status")
        mapped: XtoolStatus | None = None
        if status_data:
            mapped = _map_rest_status(status_data)
        else:
            running = await self._get_json("/device/runningStatus")
            if running and isinstance(running, dict):
                cur_mode = running.get("data", {}).get("curMode", {})
                mapped = _map_rest_mode(cur_mode.get("mode", ""))
        if mapped is not None:
            state.status = mapped

        # Job progress
        progress = await self._get_json("/processing/progress")
        if progress and isinstance(progress, dict):
            state.task_time = progress.get("totalTime", 0)

        # Fill light (REST uses 0–255 native; convert to HA 0–100 via existing scale)
        light_data = await self._get_json(REST_PATH_FILL_LIGHT)
        if light_data and isinstance(light_data, dict):
            bri = light_data.get("value", 0)
            level = round(bri * BRIGHTNESS_DEVICE_MAX / REST_BRIGHTNESS_MAX) if bri else 0
            state.fill_light_a = level
            state.fill_light_b = level

        # Cover/lid state — /peripheral/gap returns {data:{state:"on/off"}}, off = open
        gap_data = await self._get_json(REST_PATH_GAP)
        if gap_data and isinstance(gap_data, dict):
            inner = gap_data.get("data") if "data" in gap_data else gap_data
            if isinstance(inner, dict):
                gs = str(inner.get("state", "")).lower()
                if gs in ("on", "off"):
                    state.cover_open = gs == "off"

        # Air-Assist V2 connect state (M1 Ultra) — returns {state:"on"|"off"};
        # other REST models 404 silently (kept as None).
        air_data = await self._get_json(f"{REST_PATH_AIRASSIST}?action=get")
        if air_data and isinstance(air_data, dict):
            inner = air_data.get("data") if "data" in air_data else air_data
            if isinstance(inner, dict):
                aa_state = str(inner.get("state", "")).lower()
                if aa_state in ("on", "off"):
                    state.air_assist_connected = aa_state == "on"

        # Cover digital lock state (P2/P2S)
        lock_data = await self._get_json(REST_PATH_DIGITAL_LOCK)
        if lock_data and isinstance(lock_data, dict):
            inner = lock_data.get("data") if "data" in lock_data else lock_data
            if isinstance(inner, dict) and "locked" in inner:
                state.cover_locked = bool(inner.get("locked"))

        # Laser head coordinates (REST + Z-axis models)
        coord = await self._post_json(
            REST_PATH_LASER_HEAD,
            data={"action": PERIPHERAL_ACTION_GET_COORD, "waitTime": 0},
        )
        if coord and isinstance(coord, dict):
            try:
                if "x" in coord:
                    state.position_x = float(coord["x"])
                if "y" in coord:
                    state.position_y = float(coord["y"])
            except (TypeError, ValueError):
                pass

        # Device working info
        work_info = await self._get_json("/device/workingInfo")
        if work_info and isinstance(work_info, dict):
            state.task_id = work_info.get("taskId", "")

        # Flame alarm
        config = await self._post_json("/config/get", data={"keys": ["flameDetection"]})
        if config and isinstance(config, dict):
            flame = config.get("flameDetection", {})
            if isinstance(flame, dict):
                enabled = flame.get("enable", True)
                sensitivity = flame.get("sensitivity", "high")
                if not enabled:
                    state.flame_alarm = 2
                elif sensitivity == "low":
                    state.flame_alarm = 1
                else:
                    state.flame_alarm = 0

        # Air-Assist gears (default cut + engrave) — M1 Ultra user-config namespace
        if state.air_assist_connected:
            user_cfg = await self._post_json(
                "/config/get",
                data={"alias": "config", "type": "user", "kv": ["airassistCut", "airassistGrave"]},
            )
            if user_cfg and isinstance(user_cfg, dict):
                inner = user_cfg.get("data", user_cfg)
                if isinstance(inner, dict):
                    try:
                        state.air_assist_gear_cut = int(inner.get("airassistCut", 0) or 0)
                        state.air_assist_gear_grave = int(inner.get("airassistGrave", 0) or 0)
                    except (TypeError, ValueError):
                        pass

    # --- REST setter helpers (called from entities) ---

    async def set_fill_light(self, level: int) -> None:
        """Set fill light brightness (HA 0–100 → device 0–255)."""
        value = round(level * REST_BRIGHTNESS_MAX / BRIGHTNESS_DEVICE_MAX)
        await self._post(
            REST_PATH_FILL_LIGHT,
            data={"action": PERIPHERAL_ACTION_SET_BRIGHTNESS, "idx": 0, "value": value},
        )

    async def set_ir_led(self, index: int, on: bool) -> None:
        """Toggle IR LED (index 1=close-up, 2=global)."""
        action = PERIPHERAL_ACTION_ON if on else PERIPHERAL_ACTION_OFF
        await self._post(REST_PATH_IR_LED, data={"action": action, "index": index})

    async def set_digital_lock(self, locked: bool) -> None:
        """Lock or unlock the motorised cover (P2/P2S)."""
        await self._post(
            REST_PATH_DIGITAL_LOCK,
            data={"action": "lock" if locked else "unlock"},
        )

    async def move_laser_head(self, x: float, y: float, wait: int = 0) -> None:
        """Move the laser head to absolute (x, y) in device units."""
        await self._post(
            REST_PATH_LASER_HEAD,
            data={"action": PERIPHERAL_ACTION_GO_TO, "x": x, "y": y, "waitTime": wait},
        )

    async def measure_distance(self) -> float | None:
        """Trigger a single IR distance reading (P2/P2S)."""
        result = await self._post_json(
            REST_PATH_IR_DISTANCE,
            data={"action": PERIPHERAL_ACTION_GET_DISTANCE, "type": "single"},
        )
        if not result:
            return None
        for key in ("distance", "value"):
            if key in result:
                try:
                    return float(result[key])
                except (TypeError, ValueError):
                    return None
        return None

    async def set_air_assist_gear(self, target: str, value: int) -> None:
        """Set the default Air-Assist gear (target: 'cut' or 'grave') via /config/set."""
        key = "airassistCut" if target == "cut" else "airassistGrave"
        await self._post(
            "/config/set",
            data={"type": "user", "kv": {key: int(value)}},
        )

    async def set_camera_exposure(self, stream: int, value: int) -> None:
        """Set camera exposure (stream 0=overview, 1=closeup)."""
        url = (
            f"http://{self.host}:{REST_CAMERA_PORT}{REST_PATH_CAMERA_EXPOSURE}"
            f"?stream={stream}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"value": value},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.debug("Set exposure failed: HTTP %s", resp.status)
        except Exception as err:
            _LOGGER.debug("set_camera_exposure error: %s", err)

    async def get_fire_record(self) -> bytes | None:
        """Fetch the most recent flame snapshot (F1 Ultra)."""
        url = f"http://{self.host}:{REST_CAMERA_PORT}{REST_PATH_CAMERA_FIRE_RECORD}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        return await resp.read()
        except Exception as err:
            _LOGGER.debug("get_fire_record error: %s", err)
        return None

    async def pause_job(self) -> None:
        """Pause the current job."""
        await self._get("/processing/pause")

    async def resume_job(self) -> None:
        """Resume the current job."""
        await self._get("/processing/resume")

    async def cancel_job(self) -> None:
        """Cancel the current job."""
        await self._get("/processing/stop")

    # --- HTTP Helpers ---

    async def _get(self, path: str, timeout: float = 5.0) -> str | None:
        url = f"{self._base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status == 200:
                        return await resp.text()
        except Exception as err:
            _LOGGER.debug("REST GET %s failed: %s", url, err)
        return None

    async def _get_json(self, path: str, timeout: float = 5.0) -> dict | None:
        url = f"{self._base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        # Some endpoints wrap in {"code": 0, "data": {...}}
                        if isinstance(data, dict) and "data" in data:
                            return data["data"]
                        return data
        except Exception as err:
            _LOGGER.debug("REST GET JSON %s failed: %s", url, err)
        return None

    async def _post(self, path: str, data: Any = None, timeout: float = 5.0) -> str | None:
        url = f"{self._base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=data,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        return await resp.text()
        except Exception as err:
            _LOGGER.debug("REST POST %s failed: %s", url, err)
        return None

    async def _post_json(self, path: str, data: Any = None, timeout: float = 5.0) -> dict | None:
        url = f"{self._base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=data,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json(content_type=None)
                        if isinstance(result, dict) and "data" in result:
                            return result["data"]
                        return result
        except Exception as err:
            _LOGGER.debug("REST POST JSON %s failed: %s", url, err)
        return None

    # --- Firmware update overrides --------------------------------------

    async def get_firmware_versions(self, coordinator) -> dict[str, str]:
        return (
            {"main": coordinator.firmware_version}
            if coordinator.firmware_version else {}
        )

    async def flash_firmware(
        self,
        fw_file: FirmwareFile,
        data: bytes,
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """Two-step flash on port 8087: handshake then /package upload.

        Some models also require a ``machine_type`` query parameter
        (P2/P2S = ``MXP``, M1 Ultra = ``MLM``). The integration's
        XtoolFirmwareUpdate entity passes that via the protocol's
        coordinator.model.firmware_machine_type.
        """
        # The model attribute lives on the coordinator; entities pass it
        # explicitly via fw_file.board_id when needed. For now the only
        # query parameter from the model is machine_type.
        machine_type = getattr(self, "_pending_machine_type", "")
        base = f"http://{self.host}:{REST_FIRMWARE_PORT}"
        params = {"force_upgrade": "1"}
        if machine_type:
            params["machine_type"] = machine_type
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{base}/upgrade_version",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.debug(
                            "REST handshake GET returned %s; trying POST",
                            resp.status,
                        )
                        async with session.post(
                            f"{base}/upgrade_version",
                            params=params,
                            json={
                                "filename": fw_file.name,
                                "fileSize": len(data),
                            },
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as r2:
                            if r2.status != 200:
                                raise RuntimeError(
                                    f"Firmware handshake failed: HTTP {r2.status}"
                                )
            except Exception as err:
                _LOGGER.debug("REST firmware handshake error (%s) — continuing", err)

            form = aiohttp.FormData()
            form.add_field(
                "file", data, filename=fw_file.name,
                content_type="application/octet-stream",
            )
            async with session.post(
                f"{base}/package",
                data=form,
                params={"action": "burn"},
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Firmware upload failed: HTTP {resp.status}")
        if progress_cb is not None:
            progress_cb(1.0)

    def set_machine_type(self, machine_type: str) -> None:
        """Stash the model's firmware_machine_type for the next flash call."""
        self._pending_machine_type = machine_type


_REST_STATUS_MAP: dict[str, XtoolStatus] = {
    "idle": XtoolStatus.IDLE,
    "homing": XtoolStatus.INITIALIZING,
    "running": XtoolStatus.PROCESSING,
    "pause": XtoolStatus.PAUSED,
    "finish": XtoolStatus.FINISHED,
    "alarm": XtoolStatus.ERROR_LIMIT,
}

_REST_MODE_MAP: dict[str, XtoolStatus] = {
    "IDLE": XtoolStatus.IDLE,
    "HOMING": XtoolStatus.INITIALIZING,
    "PROCESSING": XtoolStatus.PROCESSING,
    "PAUSE": XtoolStatus.PAUSED,
    "FINISHED": XtoolStatus.FINISHED,
    "ALARM": XtoolStatus.ERROR_LIMIT,
    "UPGRADING": XtoolStatus.FIRMWARE_UPDATE,
}


def _map_rest_status(data: dict) -> XtoolStatus | None:
    """Map REST /cnc/status response to XtoolStatus."""
    mode = data.get("mode", "")
    sub_mode = data.get("subMode", "")
    return _REST_STATUS_MAP.get(mode) or _REST_STATUS_MAP.get(sub_mode)


def _map_rest_mode(mode: str) -> XtoolStatus | None:
    """Map REST /device/runningStatus mode string to XtoolStatus."""
    return _REST_MODE_MAP.get(mode.upper())


