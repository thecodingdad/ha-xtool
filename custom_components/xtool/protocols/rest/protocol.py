"""REST API protocol for xTool F1/F1Ultra/P2/P2S/M1/M1Ultra/P1/GS models."""

from __future__ import annotations

import json
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


def _to_bool(v) -> bool:
    """Loose bool coercion: handles 1/0, "on"/"off", "true"/"false", etc."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    return s in ("1", "true", "on", "yes", "enabled", "ok")


def _to_int(v) -> int:
    return int(v)


def _to_str(v) -> str:
    if v is None:
        return ""
    return str(v)


def _peri_float(d: dict | None) -> float | None:
    """Pull a numeric value from a /peripheral/* response in either shape:
    ``{"data":{"value":N}}`` or ``{"value":N}`` or ``{"temperature":N}``.
    """
    if not d or not isinstance(d, dict):
        return None
    inner = d.get("data", d)
    if not isinstance(inner, dict):
        return None
    for k in ("value", "temperature", "tmp", "flow", "rate"):
        if k in inner:
            try:
                return float(inner[k])
            except (TypeError, ValueError):
                return None
    return None


def _check_rest_result(body: str, endpoint: str) -> None:
    """Validate a REST firmware-endpoint JSON body. XCS uses ``Gd(t, ...)`` to
    throw whenever ``result !== "ok"``; mirror that here. Empty body is
    treated as success because some firmware responses return no body but a
    plain HTTP 200.
    """
    body = (body or "").strip()
    if not body:
        return
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        # Some endpoints return plain text "ok"
        if body.lower() == "ok":
            return
        raise RuntimeError(f"REST {endpoint} unparseable response: {body!r}")
    if isinstance(payload, dict) and payload.get("result") != "ok":
        raise RuntimeError(f"REST {endpoint} rejected: {body!r}")


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

        # Generic /get* config endpoints — universal across REST family.
        # Returned JSON shapes vary; _poll_simple_get tolerates the common ones.
        await self._poll_simple_get(state, "/getsleeptimeout", "sleep_timeout", _to_int)
        await self._poll_simple_get(state, "/getsleeptimeoutopengap", "sleep_timeout_open_gap", _to_int)
        await self._poll_simple_get(state, "/getFilllightAutoClosetimout", "fill_light_auto_off", _to_int)
        await self._poll_simple_get(state, "/getIrlightAutoClosetimout", "ir_light_auto_off", _to_int)
        await self._poll_simple_get(state, "/getBeepEnable", "beep_enabled", _to_bool)
        await self._poll_simple_get(state, "/getdrawercheck", "drawer_check", _to_bool)
        await self._poll_simple_get(state, "/getfiltercheck", "filter_check", _to_bool)
        await self._poll_simple_get(state, "/getpurifiercheck", "purifier_check", _to_bool)
        await self._poll_simple_get(state, "/getpurifiercontinue", "purifier_continue", _to_bool)
        await self._poll_simple_get(state, "/getmode", "working_mode", _to_str)
        await self._poll_simple_get(state, "/getprintToolType", "print_tool_type", _to_str)
        await self._poll_simple_get(state, "/gethardwaretype", "hardware_type", _to_str)

        # Last button event push log
        btn = await self._get_json("/peripheral/button?action=get")
        if btn and isinstance(btn, dict):
            inner = btn.get("data", btn)
            if isinstance(inner, dict):
                ev = inner.get("event") or inner.get("type") or inner.get("value")
                if ev:
                    state.last_button_event = str(ev)

        # Push peripheral state — universal
        await self._poll_peri_bool(state, "/peripheral/drawer", "drawer_open")
        await self._poll_peri_bool(state, "/peripheral/cooling_fan", "cooling_fan_running")
        await self._poll_peri_bool(state, "/peripheral/smoking_fan", "smoking_fan_running")

        # Purifier user-config (speed + flame level)
        cfg = await self._post_json(
            "/config/get",
            data={"alias": "config", "type": "user", "kv": ["purifierSpeed", "flameLevelHLSelect"]},
        )
        if cfg and isinstance(cfg, dict):
            inner = cfg.get("data", cfg)
            if isinstance(inner, dict):
                try:
                    if "purifierSpeed" in inner:
                        state.purifier_speed = int(inner.get("purifierSpeed") or 0)
                    if "flameLevelHLSelect" in inner:
                        state.flame_level_hl = int(inner.get("flameLevelHLSelect") or 0)
                except (TypeError, ValueError):
                    pass

        # Model-gated extras (poll only when capability flag set on the model).
        # The coordinator stashes the model on the protocol via ``set_model``.
        model = getattr(self, "_model", None)
        if model is not None and getattr(model, "has_water_cooling", False):
            wt = await self._get_json("/peripheral/water_tmp?action=get")
            v = _peri_float(wt)
            if v is not None:
                state.water_temperature = v
            wf = await self._get_json("/peripheral/water_flow?action=get")
            v = _peri_float(wf)
            if v is not None:
                state.water_flow = v
            await self._poll_peri_bool(state, "/peripheral/water_pump", "water_pump_running")
            await self._poll_peri_bool(state, "/peripheral/water_line", "water_line_ok")
        if model is not None and getattr(model, "has_z_temp", False):
            z = await self._get_json("/peripheral/Z_ntc_temp?action=get")
            v = _peri_float(z)
            if v is not None:
                state.z_temperature = v
        if model is not None and getattr(model, "has_workhead_id", False):
            wh = await self._get_json("/peripheral/workhead_ID?action=get")
            if wh and isinstance(wh, dict):
                inner = wh.get("data", wh)
                if isinstance(inner, dict):
                    wid = inner.get("id") or inner.get("type") or inner.get("value")
                    if wid is not None:
                        state.workhead_id = str(wid)
            zh = await self._get_json("/peripheral/workhead_ZHeight?action=get")
            v = _peri_float(zh)
            if v is not None:
                state.workhead_z_height = v
        if model is not None and getattr(model, "has_cpu_fan", False):
            await self._poll_peri_bool(state, "/peripheral/cpu_fan", "cpu_fan_running")
        if model is not None and getattr(model, "has_uv_fire", False):
            await self._poll_peri_bool(state, "/peripheral/uv_fire_sensor", "uv_fire_alarm")
        if model is not None and getattr(model, "has_display_screen", False):
            ds = await self._get_json("/peripheral/digital_screen?action=get")
            if ds and isinstance(ds, dict):
                inner = ds.get("data", ds)
                if isinstance(inner, dict):
                    for k in ("value", "brightness", "bri"):
                        if k in inner:
                            try:
                                state.display_brightness = int(inner[k])
                            except (TypeError, ValueError):
                                pass
                            break
        if model is not None and getattr(model, "has_gyro", False):
            g = await self._get_json("/peripheral/gyro?action=get")
            if g and isinstance(g, dict):
                inner = g.get("data", g)
                if isinstance(inner, dict):
                    for axis in ("x", "y", "z"):
                        for k in (f"gyro_{axis}", axis, axis.upper()):
                            if k in inner:
                                try:
                                    setattr(state, f"gyro_{axis}", float(inner[k]))
                                except (TypeError, ValueError):
                                    pass
                                break

    async def _poll_simple_get(self, state: XtoolDeviceState, path: str, attr: str, conv) -> None:
        """Poll a ``/get*`` endpoint with a tolerant JSON-shape extractor."""
        d = await self._get_json(path)
        if not d or not isinstance(d, dict):
            return
        for key in ("value", "state", "enable", "data", "type", "mode"):
            if key in d:
                v = d[key]
                if isinstance(v, dict):
                    v = v.get("value") or v.get("state") or v.get("enable") or v.get("type") or v.get("mode")
                try:
                    setattr(state, attr, conv(v))
                except (TypeError, ValueError):
                    pass
                return

    async def _poll_peri_bool(self, state: XtoolDeviceState, path: str, attr: str) -> None:
        """Poll ``/peripheral/<x>?action=get`` returning ``state:on/off``."""
        d = await self._get_json(f"{path}?action=get")
        if not d or not isinstance(d, dict):
            return
        inner = d.get("data", d)
        if isinstance(inner, dict):
            v = inner.get("state") or inner.get("value")
            if v is not None:
                s = str(v).lower()
                if s in ("on", "off", "open", "close", "closed", "true", "false", "1", "0", "ok"):
                    setattr(state, attr, s in ("on", "open", "true", "1", "ok"))

    def set_model(self, model) -> None:
        """Store the model so model-gated polls can decide to fire."""
        self._model = model

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

    # --- Generic /set* config endpoints (universal across REST family) ----

    async def set_beep_enabled(self, on: bool) -> None:
        """Toggle the device buzzer."""
        await self._post("/setBeepEnable", data={"value": 1 if on else 0})

    async def set_sleep_timeout(self, seconds: int) -> None:
        """Idle-sleep timeout (seconds)."""
        await self._post("/setsleeptimeout", data={"value": int(seconds)})

    async def set_sleep_timeout_open_gap(self, seconds: int) -> None:
        await self._post("/setsleeptimeoutopengap", data={"value": int(seconds)})

    async def set_fill_light_auto_off(self, seconds: int) -> None:
        await self._post("/setFilllightAutoClosetimout", data={"value": int(seconds)})

    async def set_ir_light_auto_off(self, seconds: int) -> None:
        await self._post("/setIrlightAutoClosetimout", data={"value": int(seconds)})

    async def set_drawer_check(self, on: bool) -> None:
        await self._post("/setdrawercheck", data={"value": 1 if on else 0})

    async def set_filter_check(self, on: bool) -> None:
        await self._post("/setfiltercheck", data={"value": 1 if on else 0})

    async def set_purifier_check(self, on: bool) -> None:
        await self._post("/setpurifiercheck", data={"value": 1 if on else 0})

    async def set_purifier_continue(self, on: bool) -> None:
        await self._post("/setpurifiercontinue", data={"value": 1 if on else 0})

    async def set_cooling_fan(self, on: bool) -> None:
        action = PERIPHERAL_ACTION_ON if on else PERIPHERAL_ACTION_OFF
        await self._post("/peripheral/cooling_fan", data={"action": action})

    async def set_smoking_fan(self, on: bool) -> None:
        action = PERIPHERAL_ACTION_ON if on else PERIPHERAL_ACTION_OFF
        await self._post("/peripheral/smoking_fan", data={"action": action})

    async def set_purifier_speed(self, value: int) -> None:
        await self._post(
            "/config/set",
            data={"type": "user", "kv": {"purifierSpeed": int(value)}},
        )

    async def set_flame_level_hl(self, value: int) -> None:
        await self._post(
            "/config/set",
            data={"type": "user", "kv": {"flameLevelHLSelect": int(value)}},
        )

    async def time_sync(self) -> None:
        """Trigger device clock sync (F1 Ultra)."""
        await self._post("/time/sync")

    async def set_display_brightness(self, value: int) -> None:
        """F1 Ultra touchscreen brightness 0-100."""
        await self._post(
            "/peripheral/digital_screen",
            data={"action": PERIPHERAL_ACTION_SET_BRIGHTNESS, "value": int(value)},
        )

    async def reboot(self) -> None:
        """Soft reboot the device."""
        await self._post("/reboot")

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
        files: list[FirmwareFile],
        blobs: list[bytes],
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """REST family flash on port 8087.

        Two strategies are supported, selected via
        ``coordinator.model.firmware_flash_strategy`` (set by ``set_strategy``):

        - ``"default"`` — F1, F1 Ultra, F1 Lite (GS005), M1 Ultra, P1, P2, P2S:
          ``GET /upgrade_version?force_upgrade=1[&machine_type=...]`` →
          ``POST /package?action=burn`` (multipart ``file=<bin>``).
        - ``"m1_four_step"`` — M1: ``POST /upgrade_version`` →
          ``POST /script`` (data) → ``POST /package`` (blob) →
          ``POST /burn?reboot=true``.

        Each step requires a JSON ``{"result":"ok"}`` response; HTTP 200
        alone is not sufficient.
        """
        if not files or not blobs:
            raise RuntimeError("REST flash: empty file list")
        strategy = getattr(self, "_pending_strategy", "default")
        if strategy == "m1_four_step":
            await self._flash_m1_four_step(files, blobs, progress_cb)
        else:
            await self._flash_default(files, blobs, progress_cb)

    async def _flash_default(
        self,
        files: list[FirmwareFile],
        blobs: list[bytes],
        progress_cb: Callable[[float], None] | None,
    ) -> None:
        """Two-step F1/P2/M1U/P1/GS005 flow: /upgrade_version then /package."""
        machine_type = getattr(self, "_pending_machine_type", "")
        base = f"http://{self.host}:{REST_FIRMWARE_PORT}"
        params = {"force_upgrade": "1"}
        if machine_type:
            params["machine_type"] = machine_type
        # Single-blob path: take the first (and typically only) file.
        fw_file = files[0]
        data = blobs[0]
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base}/upgrade_version",
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"REST firmware handshake HTTP {resp.status}"
                    )
                _check_rest_result(await resp.text(), "/upgrade_version")

            # XCS / xTool Studio post the firmware blob directly as the
            # request body (no multipart wrapping). axios sets
            # Content-Type: application/octet-stream automatically; aiohttp
            # does the same when given raw bytes.
            async with session.post(
                f"{base}/package",
                data=data,
                params={"action": "burn"},
                timeout=aiohttp.ClientTimeout(total=600),
                headers={"Content-Type": "application/octet-stream"},
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"REST /package HTTP {resp.status}"
                    )
                _check_rest_result(await resp.text(), "/package")
        if progress_cb is not None:
            progress_cb(1.0)

    async def _flash_m1_four_step(
        self,
        files: list[FirmwareFile],
        blobs: list[bytes],
        progress_cb: Callable[[float], None] | None,
    ) -> None:
        """M1 specific four-step flow: handshake → script → package → burn.

        XCS reference (``exts/M1/index.js``)::

            await apis.updateFirmwareHandshake()
            await apis.uploadFirmwareScript({data: script_payload})
            await apis.uploadFirmwarePackage({data: package_blob, onUploadProgress})
            await apis.uploadFirmwareBurn()

        The cloud delivers the firmware as two ``contents[]`` entries — the
        ``.script`` text/JSON payload first, then the ``.bin`` blob. Order in
        ``files``/``blobs`` is preserved by ``firmware.py``.
        """
        if len(files) < 2 or len(blobs) < 2:
            raise RuntimeError(
                "M1 four-step flash needs script + package files; "
                f"got {len(files)} entries"
            )
        script_data = blobs[0]
        package_data = blobs[1]

        base = f"http://{self.host}:{REST_FIRMWARE_PORT}"
        async with aiohttp.ClientSession() as session:
            # 1. handshake
            async with session.post(
                f"{base}/upgrade_version",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"M1 /upgrade_version HTTP {resp.status}"
                    )
                _check_rest_result(await resp.text(), "/upgrade_version")
            if progress_cb is not None:
                progress_cb(0.05)

            # 2. script — raw payload as POST body (no multipart wrapping
            # in XCS / xTool Studio).
            async with session.post(
                f"{base}/script",
                data=script_data,
                timeout=aiohttp.ClientTimeout(total=120),
                headers={"Content-Type": "application/octet-stream"},
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"M1 /script HTTP {resp.status}")
                _check_rest_result(await resp.text(), "/script")
            if progress_cb is not None:
                progress_cb(0.2)

            # 3. package — raw firmware blob as POST body
            async with session.post(
                f"{base}/package",
                data=package_data,
                timeout=aiohttp.ClientTimeout(total=600),
                headers={"Content-Type": "application/octet-stream"},
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"M1 /package HTTP {resp.status}")
                _check_rest_result(await resp.text(), "/package")
            if progress_cb is not None:
                progress_cb(0.85)

            # 4. burn — empty body, query string carries reboot flag
            async with session.post(
                f"{base}/burn",
                params={"reboot": "true"},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"M1 /burn HTTP {resp.status}")
                _check_rest_result(await resp.text(), "/burn")
        if progress_cb is not None:
            progress_cb(1.0)

    def set_machine_type(self, machine_type: str) -> None:
        """Stash the model's firmware_machine_type for the next flash call."""
        self._pending_machine_type = machine_type

    def set_strategy(self, strategy: str) -> None:
        """Stash the model's firmware_flash_strategy for the next flash call."""
        self._pending_strategy = strategy


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


