"""D-series protocol for xTool D1, D1 Pro, D1 Pro 2.0.

D-series exposes a REST-style HTTP API on port 8080 and a status-event
push WebSocket on port 8081. The WebSocket is read-only; all writes go
via HTTP. This module handles both channels and merges them into the
common ``XtoolDeviceState``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from ...const import FlameAlarmSensitivity, XtoolStatus
from collections.abc import Callable

from ..base import (
    DeviceInfo,
    FirmwareFile,
    LaserInfo,
    XtoolDeviceState,
    XtoolProtocol,
)


# --- D-series-owned ports ---------------------------------------------------

DSERIES_HTTP_PORT = 8080
DSERIES_WS_PORT = 8081


# --- D-series HTTP REST API (port 8080) -------------------------------------
# Endpoints documented at:
# https://github.com/1RandomDev/xTool-Connect/blob/master/XTOOL_PROTOCOL.md

DSERIES_PATH_PING = "/ping"
DSERIES_PATH_MACHINE_TYPE = "/getmachinetype"
DSERIES_PATH_LASER_POWER_INFO = "/getlaserpowerinfo"
DSERIES_PATH_PROGRESS = "/progress"
DSERIES_PATH_PERIPHERY_STATUS = "/peripherystatus"
DSERIES_PATH_SYSTEM = "/system"
DSERIES_PATH_CNC_DATA = "/cnc/data"
DSERIES_PATH_CMD = "/cmd"

# /system?action=… values
DSERIES_ACT_MAC = "mac"
DSERIES_ACT_VERSION = "version"
DSERIES_ACT_GET_WORKING_STA = "get_working_sta"
DSERIES_ACT_GET_OFFSET = "offset"
DSERIES_ACT_GET_DEV_NAME = "get_dev_name"
DSERIES_ACT_SET_DEV_NAME = "set_dev_name"
DSERIES_ACT_SET_LIMIT_STOP = "setLimitStopSwitch"
DSERIES_ACT_SET_TILT_STOP = "setTiltStopSwitch"
DSERIES_ACT_SET_MOVING_STOP = "setMovingStopSwitch"
DSERIES_ACT_SET_TILT_THRESHOLD = "setTiltCheckThreshold"
DSERIES_ACT_SET_MOVING_THRESHOLD = "setMovingCheckThreshold"
DSERIES_ACT_SET_FLAME_MODE = "setFlameAlarmMode"
DSERIES_ACT_SET_FLAME_SENSITIVITY = "setFlameAlarmSensitivity"

# /cnc/data?action=… values
DSERIES_CNC_PAUSE = "pause"
DSERIES_CNC_RESUME = "resume"
DSERIES_CNC_STOP = "stop"

# G-code sent via /cmd
CMD_QUIT_LIGHTBURN_MODE = "M112 N0"
CMD_REDCROSS_MODE = "M97"  # M97 S0=cross-laser pointer, S1=low-light mode

# /system?action=get_working_sta returns "0" / "1" / "2"
DSERIES_STATUS_MAP: dict[str, XtoolStatus] = {
    "0": XtoolStatus.IDLE,
    "1": XtoolStatus.WORKING_API,
    "2": XtoolStatus.WORKING_BUTTON,
}

# WebSocket push frames (text only, port 8081)
DSERIES_WS_EVENT_MAP: dict[str, XtoolStatus] = {
    "ok:IDLE": XtoolStatus.IDLE,
    "ok:WORKING_ONLINE": XtoolStatus.PROCESSING,
    "ok:WORKING_ONLINE_READY": XtoolStatus.PROCESSING_READY,
    "ok:WORKING_OFFLINE": XtoolStatus.WORKING_BUTTON,
    "ok:WORKING_FRAMING": XtoolStatus.FRAMING,
    "ok:WORKING_FRAME_READY": XtoolStatus.FRAME_READY,
    "ok:PAUSING": XtoolStatus.PAUSED,
    "WORK_STOPPED": XtoolStatus.CANCELLING,
    "ok:ERROR": XtoolStatus.ERROR_LIMIT,
    "err:flameCheck": XtoolStatus.ERROR_FIRE_WARNING,
    "err:tiltCheck": XtoolStatus.ERROR_TILT,
    "err:movingCheck": XtoolStatus.ERROR_MOVING,
    "err:limitCheck": XtoolStatus.ERROR_LIMIT,
}

# D-series uses 1/2/3 for high/low/off — convert to/from FlameAlarmSensitivity
DSERIES_FLAME_SENSITIVITY_MAP: dict[int, FlameAlarmSensitivity] = {
    1: FlameAlarmSensitivity.HIGH,
    2: FlameAlarmSensitivity.LOW,
    3: FlameAlarmSensitivity.OFF,
}
DSERIES_FLAME_SENSITIVITY_REVERSE: dict[FlameAlarmSensitivity, int] = {
    v: k for k, v in DSERIES_FLAME_SENSITIVITY_MAP.items()
}

_LOGGER = logging.getLogger(__name__)


class DSeriesProtocol(XtoolProtocol):
    """D1 / D1 Pro / D1 Pro 2.0 protocol."""

    def __init__(self, host: str, port: int = DSERIES_HTTP_PORT,
                 ws_port: int = DSERIES_WS_PORT) -> None:
        super().__init__(host)
        self._port = port
        self._ws_port = ws_port
        self._base_url = f"http://{host}:{port}"
        self._ws_task: asyncio.Task[None] | None = None
        self._ws_event_status: XtoolStatus | None = None

    @property
    def connected(self) -> bool:
        # HTTP is stateless; WS is best-effort and not required.
        return True

    async def connect(self) -> None:
        # Start the optional WS listener once.
        if self._ws_task is None or self._ws_task.done():
            self._ws_task = asyncio.create_task(self._ws_loop())

    async def disconnect(self) -> None:
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
        self._ws_task = None

    async def send_command(self, command: str, timeout: float = 5.0) -> str:
        """Forward a raw G-code string to /cmd. Used by the buttons platform."""
        url = f"{self._base_url}/cmd"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, data=command,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    headers={"Content-Type": "text/plain"},
                ) as resp:
                    if resp.status == 200:
                        return (await resp.text()).strip()
        except Exception as err:
            _LOGGER.debug("D-series /cmd %s failed: %s", command, err)
        return ""

    async def get_version(self) -> str | None:
        data = await self._system_action(DSERIES_ACT_VERSION)
        return data.get("version") if data else None

    async def get_device_info(self) -> DeviceInfo:
        info = DeviceInfo()

        # /system?action=version → {"sn", "version"}
        version_data = await self._system_action(DSERIES_ACT_VERSION)
        _LOGGER.debug("D-series /system?action=version raw: %s", version_data)
        if version_data:
            info.serial_number = str(version_data.get("sn", ""))
            info.main_firmware = str(version_data.get("version", ""))

        # /getmachinetype → {"type"}
        mt = await self._get_json(DSERIES_PATH_MACHINE_TYPE)
        _LOGGER.debug("D-series /getmachinetype raw: %s", mt)
        if mt:
            info.device_name = str(mt.get("type", ""))

        # Override device name with user-set name if present
        name_data = await self._system_action(DSERIES_ACT_GET_DEV_NAME)
        if name_data and name_data.get("name"):
            info.device_name = str(name_data["name"])

        # /getlaserpowerinfo → {"type", "power"}
        laser_data = await self._get_json(DSERIES_PATH_LASER_POWER_INFO)
        if laser_data:
            info.laser = LaserInfo(
                laser_type=int(laser_data.get("type", 0) or 0),
                power_watts=int(laser_data.get("power", 0) or 0),
            )

        # MAC address
        mac_data = await self._system_action(DSERIES_ACT_MAC)
        if mac_data and mac_data.get("mac"):
            info.mac_address = str(mac_data["mac"])

        return info

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Poll D-series HTTP endpoints and merge any push event."""
        # Working state — 0/1/2 ASCII string
        ws_data = await self._system_action(DSERIES_ACT_GET_WORKING_STA)
        _LOGGER.debug("D-series /system?action=getworkingsta raw: %s", ws_data)
        if ws_data:
            raw = str(ws_data.get("working", "0"))
            mapped = DSERIES_STATUS_MAP.get(raw)
            if mapped is None:
                _LOGGER.debug(
                    "D-series unknown working-state code %r — please report",
                    raw,
                )
                mapped = XtoolStatus.UNKNOWN
            # WS push events take priority — they carry transient errors
            if self._ws_event_status is not None:
                mapped = self._ws_event_status
                self._ws_event_status = None
            state.status = mapped

        # Job progress
        prog = await self._get_json(DSERIES_PATH_PROGRESS)
        _LOGGER.debug("D-series /getprogress raw: %s", prog)
        if prog:
            try:
                state.task_time = int(prog.get("working", 0)) // 1000
            except (TypeError, ValueError):
                pass

        # Peripheral status — sd card, safety flags, thresholds, flame sens.
        peri = await self._get_json(DSERIES_PATH_PERIPHERY_STATUS)
        _LOGGER.debug("D-series /peripherystatus raw: %s", peri)
        if peri:
            state.sd_card_present = peri.get("sdCard") == 1
            state.tilt_stop_enabled = peri.get("tiltStopFlag") == 1
            state.moving_stop_enabled = peri.get("movingStopFlag") == 1
            state.limit_stop_enabled = peri.get("limitStopFlag") == 1
            # Keep generic move_stop_enabled in sync with movingStopFlag only;
            # the other two are now exposed as their own switches.
            state.move_stop_enabled = state.moving_stop_enabled
            try:
                state.tilt_threshold = int(peri.get("tiltThreshold", 0))
                state.moving_threshold = int(peri.get("movingThreshold", 0))
                state.flame_alarm_mode = int(peri.get("flameAlarmMode", 0))
            except (TypeError, ValueError):
                pass
            sensitivity = peri.get("flameAlarmSensitivity")
            if isinstance(sensitivity, int):
                mapped_sens = DSERIES_FLAME_SENSITIVITY_MAP.get(sensitivity)
                if mapped_sens is not None:
                    state.flame_alarm = int(mapped_sens)

        # Origin offset
        offset = await self._system_action(DSERIES_ACT_GET_OFFSET)
        _LOGGER.debug("D-series /system?action=getoffset raw: %s", offset)
        if offset:
            try:
                state.origin_offset_x = float(offset.get("x", 0) or 0)
                state.origin_offset_y = float(offset.get("y", 0) or 0)
            except (TypeError, ValueError):
                pass

        # Summary — single line per poll for quick verification.
        _LOGGER.debug(
            "D-series poll resolved: status=%s task_time=%s "
            "tilt_thr=%s moving_thr=%s flame_mode=%s sd=%s",
            state.status, state.task_time, state.tilt_threshold,
            state.moving_threshold, state.flame_alarm_mode,
            state.sd_card_present,
        )


    async def _system_action(self, action: str) -> dict[str, Any] | None:
        return await self._get_json(
            f"{DSERIES_PATH_SYSTEM}?action={action}"
        )

    async def _get_json(self, path: str, timeout: float = 5.0) -> dict[str, Any] | None:
        url = f"{self._base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status != 200:
                        return None
                    payload = await resp.json(content_type=None)
                    if isinstance(payload, dict) and payload.get("result") == "fail":
                        return None
                    return payload if isinstance(payload, dict) else None
        except Exception as err:
            _LOGGER.debug("D-series GET %s failed: %s", path, err)
            return None

    # --- Setters used by entities ---

    async def set_limit_stop(self, enabled: bool) -> None:
        await self._system_call(
            DSERIES_ACT_SET_LIMIT_STOP, {"limitStopSwitch": int(enabled)}
        )

    async def set_tilt_stop(self, enabled: bool) -> None:
        await self._system_call(
            DSERIES_ACT_SET_TILT_STOP, {"tiltStopSwitch": int(enabled)}
        )

    async def set_moving_stop(self, enabled: bool) -> None:
        await self._system_call(
            DSERIES_ACT_SET_MOVING_STOP, {"movingStopSwitch": int(enabled)}
        )

    async def set_tilt_threshold(self, value: int) -> None:
        await self._system_call(
            DSERIES_ACT_SET_TILT_THRESHOLD, {"tiltCheckThreshold": int(value)}
        )

    async def set_moving_threshold(self, value: int) -> None:
        await self._system_call(
            DSERIES_ACT_SET_MOVING_THRESHOLD, {"movingCheckThreshold": int(value)}
        )

    async def set_flame_alarm_mode(self, value: int) -> None:
        await self._system_call(
            DSERIES_ACT_SET_FLAME_MODE, {"flameAlarmMode": int(value)}
        )

    async def set_flame_alarm_sensitivity(
        self, sensitivity: FlameAlarmSensitivity
    ) -> None:
        device_value = DSERIES_FLAME_SENSITIVITY_REVERSE.get(sensitivity)
        if device_value is None:
            return
        await self._system_call(
            DSERIES_ACT_SET_FLAME_SENSITIVITY,
            {"flameAlarmSensitivity": device_value},
        )

    async def quit_lightburn_mode(self) -> None:
        """Send M112 N0 to leave LightBurn standby."""
        await self.send_command(CMD_QUIT_LIGHTBURN_MODE)

    async def set_redcross_mode(self, mode: int) -> None:
        """M97 S0 = cross-laser pointer, M97 S1 = low-light mode."""
        await self.send_command(f"{CMD_REDCROSS_MODE} S{int(mode)}")

    async def set_work_area_limits(self, left: int, right: int, up: int, down: int) -> None:
        """M311 L<l> R<r> U<u> D<d> — work-area soft limits in mm."""
        await self.send_command(f"M311 L{int(left)} R{int(right)} U{int(up)} D{int(down)}")

    async def flash_firmware(
        self,
        files: list[FirmwareFile],
        blobs: list[bytes],
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """POST /upgrade with the firmware blob (D-series ESP32 OTA).

        Mirrors the xTool Studio Windows app's flow:
        ``Content-Type: multipart/form-data`` with a single field named
        ``firmwareData`` carrying the raw firmware bytes (no explicit blob
        type, no filename). The device handles bootloader entry internally.

        Older XCS Android revisions used field name ``file`` and a
        ``application/macbinary`` blob type — both styles seem to be
        accepted by the firmware, but xTool Studio is the current
        reference, so we match it.

        Response body is the literal string ``"OK"`` on success (HTTP 200
        alone is insufficient — the device sometimes returns 200 with an
        error string).
        """
        if not files or not blobs:
            raise RuntimeError("D-series flash: empty file list")
        data = blobs[0]
        url = f"{self._base_url}/upgrade"
        form = aiohttp.FormData()
        form.add_field("firmwareData", data, filename="firmware.bin")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=form,
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"D-series firmware upload failed: HTTP {resp.status}"
                    )
                body = (await resp.text()).strip()
                if body and body.upper() != "OK":
                    # Some firmware returns JSON {"result":"OK"}; accept that too.
                    try:
                        import json as _json
                        payload = _json.loads(body)
                        if str(payload.get("result", "")).upper() == "OK":
                            body = ""
                    except (ValueError, TypeError):
                        pass
                    if body:
                        raise RuntimeError(
                            f"D-series /upgrade rejected: {body!r}"
                        )
        if progress_cb is not None:
            progress_cb(1.0)

    async def _system_call(self, action: str, params: dict) -> None:
        """Issue a /system?action=… GET with extra query params."""
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = f"{DSERIES_PATH_SYSTEM}?action={action}&{query}"
        await self._get_json(path)

    async def validate(self):  # type: ignore[override]
        """Probe /ping + /getmachinetype before returning ConnectionInfo."""
        ping = await self._get_json(DSERIES_PATH_PING)
        if not ping or ping.get("result") != "ok":
            return None
        return await super().validate()

    # --- WebSocket push listener (status events only, no commands) ---

    async def _ws_loop(self) -> None:
        """Stay connected to the D-series WS and absorb status events."""
        url = f"ws://{self.host}:{self._ws_port}/"
        backoff = 5.0
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        url, timeout=10, heartbeat=30
                    ) as ws:
                        backoff = 5.0
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                self._handle_event(msg.data.strip())
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug("D-series WS error (%s): retrying in %ss", err, backoff)
            await asyncio.sleep(backoff)

    def _handle_event(self, frame: str) -> None:
        status = DSERIES_WS_EVENT_MAP.get(frame)
        if status is not None:
            self._ws_event_status = status
            _LOGGER.debug("D-series WS event %r → %s", frame, status)
        else:
            _LOGGER.debug(
                "D-series WS event %r unmapped — please report so it can be "
                "wired to a status",
                frame,
            )


