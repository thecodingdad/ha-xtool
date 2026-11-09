"""xTool P2: the REST API on port 8080 plus the push channel xTool Studio uses.

Studio's P2 extension (``exts.zip`` ``P2/index.js``) keeps a WebSocket open
to ``ws://<laser>:8081`` and sends the text ``ping`` every 3 seconds. The
laser answers with a 10-byte binary keepalive every 2 seconds and pushes
every change as a binary frame: a 10-byte header (bytes 2-5: the length of
the JSON that follows, little-endian) and then JSON such as::

    {"module": "STATUS_CONTROLLER", "type": "MODE_CHANGE",
     "info": {"mode": "Work", "subMode": "working", "taskId": "..."},
     "level": "notice"}
    {"module": "GAP", "type": "OPEN", "info": "", "level": "notice"}

On connect the laser pushes its current mode at once. When neither a push
nor a keepalive arrives for 11 seconds (Studio's heartbeat timeout), the
laser is gone.

The poll reads what Studio reads (``/device/runningStatus``,
``/processing/progress`` and the peripherals) over one reused HTTP
session. Settings change rarely and are read once a minute.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from ...const import XtoolStatus
from ..base import DeviceInfo, XtoolDeviceState
from .protocol import (
    PERIPHERAL_ACTION_GET_COORD,
    PERIPHERAL_ACTION_GET_DISTANCE,
    PERIPHERAL_ACTION_GO_TO,
    REST_BRIGHTNESS_MAX,
    REST_CAMERA_PORT,
    REST_HTTP_PORT,
    REST_PATH_DIGITAL_LOCK,
    REST_PATH_FILL_LIGHT,
    REST_PATH_GAP,
    REST_PATH_IR_DISTANCE,
    REST_PATH_IR_LED,
    REST_PATH_LASER_HEAD,
    RestProtocol,
    _map_rest_mode,
    _peri_float,
)

_LOGGER = logging.getLogger(__name__)

P2_PUSH_PORT = 8081
# Studio: heartbeatInterval 3000 ms with pingCmd "ping", heartbeatTimeout 11000 ms
PING_SECONDS = 3.0
PUSH_TIMEOUT_SECONDS = 11.0
CONNECT_TIMEOUT_SECONDS = 5.0
FRAME_HEADER_BYTES = 10
# Settings and slow-moving values are read this often, not every poll
SLOW_READ_SECONDS = 60.0
# Studio's laser head moves: speed (mm/min) and how long the P2 may take
P2_HEAD_SPEED = 9600
P2_HEAD_WAIT_MS = 30000
# Studio's limits for laser head moves, mm from home
P2_HEAD_MAX_X = 620.0
P2_HEAD_MAX_Y = 308.0

# Studio's config keys for the P2 (its deviceInfo read), plus Baseplate detection
P2_CONFIG_KEYS: tuple[str, ...] = (
    "offlineProtoModeEnable", "offlineProtoProModeEnable", "offlineOptionModeEnable",
    "flameAlarm", "flameSensitivity", "fillLightBrightness", "purifierTimeout",
    "motionPowerCut", "laserMeasureOffset", "flameLevelHLSelect", "purifierEnable",
    "beepEnable", "taskId", "flameAlarmLevel1", "drawerCheck",
)

# machineInfo machineSubType -> Studio's Laser safety classification
P2_SAFETY_CLASSES: dict[str, str] = {"001": "Class 1", "004": "Class 4"}

# Studio's P2 error table (P2/index.js): code, "<module>.<type>.<info>",
# Studio's English title, and the Error event type it is reported as.
# The flame alarm (Qx_x_3) is reported by the Fire warning event instead.
P2_ERRORS: tuple[tuple[str, str, str, str], ...] = (
    ("Qx30_0", "FIRMWARE.NOT_MATCHED.", "Firmware version info not found", "firmware"),
    ("Qx30_1", "BOARDS.ELEMENT_NOT_FOUND.", "Board communication failure", "board_fault"),
    ("Qx30_1_1", "BOARDS.ELEMENT_NOT_FOUND.motion_gd450", "Board communication failure", "board_fault"),
    ("Qx30_1_2", "BOARDS.ELEMENT_NOT_FOUND.ui_gd330", "Board communication failure", "board_fault"),
    ("Qx30_1_3", "BOARDS.ELEMENT_NOT_FOUND.wcb_gd330", "Board communication failure", "board_fault"),
    ("Qx16", "CONTROLLER.TMP_HIGH.", "Main control board temperature exception", "temperature"),
    ("Qx16_1", "CONTROLLER.TMP_HIGH.MASTER", "Main control board temperature exception", "temperature"),
    ("Qx01_2", "WATER_TMP.ELEMENT_NOT_FOUND.", "Water tank temperature sensor exception", "water_cooling"),
    ("Qx01_1", "WATER_TMP.TMP_HIGH.", "Water tank temperature too high", "water_cooling"),
    ("Qx01_0", "WATER_TMP.TMP_LOW.", "Water tank temperature too low", "water_cooling"),
    ("Qx30_2", "WATER_LINE.VALUE_LOW.", "Water level in the water tank too low", "water_cooling"),
    ("Qx08_0", "WATER_PUMP.CUR_LOW.", "Water pump current too low", "water_cooling"),
    ("Qx08_1", "WATER_PUMP.CUR_HIGH.", "Water pump current too high", "water_cooling"),
    ("Qx08_2", "WATER_PUMP.CUR_ABNORMAL.", "Water pump current exception", "water_cooling"),
    ("Qx30_3", "WATER_FAN.CUR_ABNORMAL.", "Water-cooled fan exception", "water_cooling"),
    ("Qx30_3_1", "WATER_FAN.CUR_ABNORMAL.1", "Water-cooled fan exception", "water_cooling"),
    ("Qx30_3_2", "WATER_FAN.CUR_ABNORMAL.2", "Water-cooled fan exception", "water_cooling"),
    ("Qx30_3_3", "WATER_FAN.CUR_ABNORMAL.3", "Water-cooled fan exception", "water_cooling"),
    ("Qx30_4", "WATER_FLOW.VALUE_LOW.", "Water tank flow too low", "water_cooling"),
    ("Qx14_4", "MOTOR_DRIVER.ELEMENT_ABNORMAL.M23 S135", "X-axis driver exception", "moving"),
    ("Qx14_5", "MOTOR_DRIVER.ELEMENT_ABNORMAL.M23 S136", "Y-axis driver exception", "moving"),
    ("Qx14_6", "MOTOR_DRIVER.ELEMENT_ABNORMAL.M23 S137", "Z-axis driver exception", "moving"),
    ("Qx14_7", "MOTOR_DRIVER.ELEMENT_ABNORMAL.M23 S138", "U-axis driver exception", "moving"),
    ("Qx14", "MOTOR_LIMIT.ELEMENT_NOT_FOUND.", "Motion axis limit error", "limit"),
    ("Qx14_1", "MOTOR_LIMIT.OPERATION_FAILED.M23 S140", "X-axis homing failure", "limit"),
    ("Qx14_2", "MOTOR_LIMIT.OPERATION_FAILED.M23 S141", "Y-axis homing failure", "limit"),
    ("Qx14_3", "MOTOR_LIMIT.OPERATION_FAILED.M23 S142", "Z-axis homing failure", "limit"),
    ("Qx14_8", "MOTOR_LIMIT.OPERATION_FAILED.M23 S143", "U-axis homing failure", "limit"),
    ("Qx30_5", "MOTOR_LIMIT.OPERATION_FAILED.M23 S148", "X-axis initialization exception", "limit"),
    ("Qx30_6", "MOTOR_LIMIT.OPERATION_FAILED.M23 S149", "Y-axis initialization exception", "limit"),
    ("Qx30_7", "MOTOR_LIMIT.OPERATION_FAILED.M23 S150", "Z-axis initialization exception", "limit"),
    ("Qx30_8", "MOTOR_LIMIT.OPERATION_FAILED.M23 S151", "U-axis initialization exception", "limit"),
    ("Qx32_0", "SMOKING_FAN.CUR_LOW.", "Fan current too low", "exhaust_fan"),
    ("Qx32_1", "SMOKING_FAN.CUR_HIGH.", "Fan current too high", "exhaust_fan"),
    ("Qx17_0", "CAMERA.ELEMENT_NOT_FOUND.", "Camera not found", "camera_fault"),
    ("Qx17_1", "CAMERA.OPERATION_FAILED.", "Camera exception", "camera_fault"),
    ("Qx31_1", "ELECTRONICLOCK.OPERATION_FAILED.NOT OPEN", "Safety lock unlocking exception", "safety_lock"),
    ("Qx31_0", "ELECTRONICLOCK.OPERATION_FAILED.NOT CLOSE", "Safety lock locking exception", "safety_lock"),
    ("Qx31_2", "AIRPUMP.CUR_ABNORMAL.", "Air pump current exception", "air_pump"),
    ("Qx_x_1", "GYRO_SENSOR.MACHINE_TILTED.", "Device tilted", "tilt"),
    ("Qx_x_2", "SHORT_CIRCUIT_PROTECT.GND_SHORT_CIRCUTE.", "Device casing short-circuited with the circuit board", "short_circuit"),
    ("Qx_x_3", "FIRE_RECOGNITION.FIND_EXCEPTION.", "Flame detected", "fire"),
)
_ERRORS_BY_KEY: dict[str, tuple[str, str, str]] = {
    key: (code, title, kind) for code, key, title, kind in P2_ERRORS
}
# The Error event types the P2 reports
P2_ERROR_EVENT_TYPES: tuple[str, ...] = tuple(
    dict.fromkeys(kind for _, _, _, kind in P2_ERRORS if kind != "fire")
) + ("other",)

# Studio's getCurrentStatus: "<mode>.<subMode>" -> status. "Work" carries the
# job's phase in its sub-mode.
_WORK_SUB_MODES: dict[str, XtoolStatus] = {
    "WORKREADY": XtoolStatus.PROCESSING_READY,
    "WORKING": XtoolStatus.PROCESSING,
    "WORKPAUSE": XtoolStatus.PAUSED,
}


def map_p2_mode(mode: Any, sub_mode: Any = "") -> XtoolStatus | None:
    """The status for a P2 mode, as Studio reads it."""
    mode_s = str(mode or "").upper()
    sub_s = str(sub_mode or "").upper()
    if mode_s == "WORK":
        status = _WORK_SUB_MODES.get(sub_s)
        if status is None:
            _LOGGER.debug("P2 job sub-mode %r unknown; showing Processing", sub_mode)
            return XtoolStatus.PROCESSING
        return status
    status = _map_rest_mode(mode_s)
    if status is None and mode_s:
        _LOGGER.debug("P2 mode %r (sub-mode %r) unknown, please report", mode, sub_mode)
    return status


def decode_push_frame(data: bytes | str) -> dict[str, Any] | None:
    """The JSON of one push frame, or None for a keepalive or a frame we cannot read."""
    if isinstance(data, str):
        text = data
    else:
        if len(data) < FRAME_HEADER_BYTES:
            return None
        length = int.from_bytes(data[2:6], "little")
        body = data[FRAME_HEADER_BYTES:FRAME_HEADER_BYTES + length] if length else b""
        if not body:
            return None
        text = body.decode("utf-8", "replace")
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        _LOGGER.debug("P2 push not JSON: %r", text[:200])
        return None
    return obj if isinstance(obj, dict) else None


def describe_alarm(entry: dict[str, Any]) -> dict[str, Any] | None:
    """An alarm as Studio shows it (code, message, level, event type), or None.

    Studio looks up ``<module>.<type>.<info>``; an ``info`` holding several
    values separated by ``:`` is looked up value by value.
    """
    module = str(entry.get("module") or "")
    kind = str(entry.get("type") or "")
    if not module or not kind:
        return None
    info = entry.get("info")
    info_s = "" if info is None or isinstance(info, dict) else str(info)
    keys = [f"{module}.{kind}.{info_s}"]
    keys += [f"{module}.{kind}.{part}" for part in info_s.split(":") if part]
    keys.append(f"{module}.{kind}.")
    for key in keys:
        if key in _ERRORS_BY_KEY:
            code, title, event_type = _ERRORS_BY_KEY[key]
            return {
                "code": code,
                "message": title,
                "level": entry.get("level"),
                "event_type": event_type,
            }
    return None


class P2Protocol(RestProtocol):
    """The P2's REST API, with Studio's push channel for live changes."""

    # /peripheral/gap reports "off" while the lid is closed (Studio: isCover)
    GAP_OFF_MEANS_OPEN = False

    def __init__(self, host: str, port: int = REST_HTTP_PORT) -> None:
        super().__init__(host, port)
        self._push_session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._last_frame = 0.0
        # Field -> (value, time it was pushed); applied over each poll
        self._latest: dict[str, tuple[Any, float]] = {}
        # (kind, event type, attributes) for the coordinator to send as events
        self._push_events: list[tuple[str, str, dict[str, Any]]] = []
        self._push_notify: Callable[[], None] | None = None
        self._http: aiohttp.ClientSession | None = None
        self._next_slow_read = 0.0
        self.machine_info: dict[str, Any] = {}
        # Set by a WORK_STOPED push: the job that ends next was cancelled
        self._stopping = False
        # Studio's Fill light brightness as last read or written
        self._studio_brightness: int | None = None

    # --- Connection -----------------------------------------------------

    @property
    def connected(self) -> bool:
        """True while the push channel is open and the laser is heard from."""
        return (
            self._ws is not None
            and not self._ws.closed
            and time.monotonic() - self._last_frame < PUSH_TIMEOUT_SECONDS
        )

    async def connect(self) -> None:
        """Open the push channel; raises when the laser does not answer."""
        if self.connected:
            return
        await self._close_push()
        self._push_session = aiohttp.ClientSession()
        url = f"ws://{self.host}:{P2_PUSH_PORT}/"
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                self._ws = await self._push_session.ws_connect(url, autoping=True)
        except Exception:
            await self._close_push()
            raise
        self._last_frame = time.monotonic()
        _LOGGER.debug("P2 push channel open: %s", url)
        self._reader = asyncio.create_task(self._read_pushes(self._ws), name=f"xtool_p2_push_{self.host}")

    async def disconnect(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None and reader is not asyncio.current_task() and not reader.done():
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass
        await self._close_push()
        http, self._http = self._http, None
        if http is not None and not http.closed:
            await http.close()

    async def _close_push(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass
        session, self._push_session = self._push_session, None
        if session is not None and not session.closed:
            await session.close()

    async def _read_pushes(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Read pushes until the channel closes or the laser goes quiet."""
        next_ping = 0.0
        try:
            while not ws.closed:
                now = time.monotonic()
                if now >= next_ping:
                    await ws.send_str("ping")
                    next_ping = now + PING_SECONDS
                try:
                    msg = await ws.receive(timeout=max(0.1, next_ping - time.monotonic()))
                except asyncio.TimeoutError:
                    if time.monotonic() - self._last_frame >= PUSH_TIMEOUT_SECONDS:
                        _LOGGER.debug("P2 push channel silent for %.0f s", PUSH_TIMEOUT_SECONDS)
                        break
                    continue
                if msg.type in (aiohttp.WSMsgType.BINARY, aiohttp.WSMsgType.TEXT):
                    self._last_frame = time.monotonic()
                    obj = decode_push_frame(msg.data)
                    if obj is not None:
                        self.handle_push(obj)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("P2 push channel failed: %s", err)
        _LOGGER.debug("P2 push channel closed")
        if self._ws is ws:
            self._last_frame = 0.0
            try:
                await ws.close()
            except Exception:
                pass
        self._notify()

    def _notify(self) -> None:
        if self._push_notify is not None:
            self._push_notify()

    # --- Pushes ---------------------------------------------------------

    def _set(self, field: str, value: Any) -> None:
        self._latest[field] = (value, time.monotonic())

    def handle_push(self, obj: dict[str, Any]) -> None:
        """Record what one push says and tell the coordinator."""
        _LOGGER.debug("P2 push: %s", obj)
        module = str(obj.get("module") or "")
        kind = str(obj.get("type") or "")
        info = obj.get("info")
        if module == "STATUS_CONTROLLER":
            if kind == "MODE_CHANGE" and isinstance(info, dict):
                status = map_p2_mode(info.get("mode"), info.get("subMode"))
                if status is not None:
                    self._set("status", self._after_stop(status))
                self._set("working_mode", str(info.get("subMode") or ""))
                if info.get("taskId"):
                    self._set("task_id", str(info["taskId"]))
            elif kind == "WORK_STOPED":
                # Studio: a stop and then the job ending is a cancelled job
                self._stopping = True
                self._set("status", XtoolStatus.CANCELLING)
            elif kind == "WORK_PREPARED":
                self._stopping = False
        elif module == "GAP" and kind in ("OPEN", "CLOSE"):
            self._set("cover_open", kind == "OPEN")
        elif module == "DRAWER" and kind in ("OPEN", "CLOSE"):
            self._set("drawer_open", kind == "OPEN")
        elif module == "BUTTON" and kind:
            from .protocol import _normalise_rest_button_event

            event = _normalise_rest_button_event(kind)
            if event:
                self._push_events.append(("button", event, {"raw_type": kind}))
        elif module == "FIRE_RECOGNITION" and kind == "FIND_EXCEPTION":
            self._push_events.append(("fire_warning", "triggered", {"level": info}))
        else:
            alarm = describe_alarm(obj)
            if alarm is not None:
                self._push_events.append(("error", alarm["event_type"], {
                    "code": alarm["code"], "message": alarm["message"], "level": alarm["level"],
                }))
        self._notify()

    def _after_stop(self, status: XtoolStatus) -> XtoolStatus:
        """After a stop, the job ending reads Cancelling instead of Finished."""
        if not self._stopping:
            return status
        if status == XtoolStatus.FINISHED:
            return XtoolStatus.CANCELLING
        if status != XtoolStatus.CANCELLING:
            self._stopping = False
        return status

    def apply_latest(self, state: XtoolDeviceState, since: float = 0.0) -> None:
        """Copy pushed values newer than ``since`` into ``state``."""
        for field, (value, at) in self._latest.items():
            if at >= since:
                setattr(state, field, value)

    def take_push_events(self) -> list[tuple[str, str, dict[str, Any]]]:
        events, self._push_events = self._push_events, []
        return events

    # --- Polling --------------------------------------------------------

    def _map_running_mode(self, cur_mode: dict[str, Any]) -> XtoolStatus | None:
        return map_p2_mode(cur_mode.get("mode"), cur_mode.get("subMode"))

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Read what Studio reads, then keep pushes newer than the reads."""
        started = time.monotonic()
        self._offline_until = 0.0
        running = await self._get_json("/device/runningStatus")
        if self._is_offline():
            raise ConnectionError(f"P2 at {self.host} did not answer")
        if isinstance(running, dict):
            self._apply_running_status(state, running)
        self._apply_progress(state, await self._get_json("/processing/progress"))
        replies = {}
        for path in (REST_PATH_GAP, "/peripheral/drawer", REST_PATH_DIGITAL_LOCK,
                     REST_PATH_FILL_LIGHT, REST_PATH_IR_LED):
            replies[path] = await self._get_json(path)
        self._apply_peripherals(state, replies)
        # The P2 pushes no settings changes (Studio's included): read them
        # on every poll, so a change shows within one poll
        await self._read_config(state)
        # Where the head is, on every poll: it moves while the P2 homes
        # after power on, and with Studio's moves
        await self._read_head_position(state)
        if started >= self._next_slow_read:
            await self._read_slow(state)
            if not self._is_offline():
                self._next_slow_read = started + SLOW_READ_SECONDS
        if self._is_offline():
            raise ConnectionError(f"P2 at {self.host} did not answer")
        # A push that arrived while the poll ran is newer than what the poll read
        self.apply_latest(state, since=started)
        _LOGGER.debug(
            "P2 poll: status=%s progress=%s time=%s lid_open=%s alarms=%s",
            state.status, state.task_progress, state.task_time,
            state.cover_open, [a.get("code") for a in state.active_alarms],
        )

    def _apply_running_status(self, state: XtoolDeviceState, running: dict[str, Any]) -> None:
        cur_mode = running.get("curMode")
        if isinstance(cur_mode, dict):
            status = self._map_running_mode(cur_mode)
            if status is not None:
                state.status = self._after_stop(status)
            state.working_mode = str(cur_mode.get("subMode") or "")
            if cur_mode.get("taskId"):
                state.task_id = str(cur_mode["taskId"])
        if isinstance(running.get("cpuTemp"), (int, float)):
            state.cpu_temp = int(running["cpuTemp"])
        alarm_info = running.get("curAlarmInfo")
        if isinstance(alarm_info, dict):
            raw = alarm_info.get("alarm")
            alarms = []
            for entry in raw if isinstance(raw, list) else []:
                if not isinstance(entry, dict):
                    continue
                alarms.append(describe_alarm(entry) or {
                    "code": None,
                    "message": " ".join(
                        str(entry.get(key) or "") for key in ("module", "type", "info")
                    ).strip(),
                    "level": entry.get("level"),
                    "event_type": "other",
                })
            state.active_alarms = alarms

    @staticmethod
    def _apply_progress(state: XtoolDeviceState, progress: Any) -> None:
        """``{"time": <seconds>, "total": 100, "value": <done>}``."""
        if not isinstance(progress, dict):
            return
        try:
            total = float(progress.get("total") or 0)
            value = float(progress.get("value") or 0)
            state.task_progress = max(0.0, min(100.0, value * 100.0 / total)) if total else None
            state.task_time = int(progress.get("time") or 0)
        except (TypeError, ValueError):
            pass

    def _apply_peripherals(self, state: XtoolDeviceState, replies: dict[str, Any]) -> None:
        def on_off(path: str) -> bool | None:
            reply = replies.get(path)
            value = str(reply.get("state", "")).lower() if isinstance(reply, dict) else ""
            return {"on": True, "off": False}.get(value)

        gap = on_off(REST_PATH_GAP)
        if gap is not None:
            # "off" is a closed lid (Studio: isCover)
            state.cover_open = gap
        drawer = on_off("/peripheral/drawer")
        if drawer is not None:
            state.drawer_open = drawer
        lock = on_off(REST_PATH_DIGITAL_LOCK)
        if lock is not None:
            state.cover_locked = lock
        lights = replies.get(REST_PATH_FILL_LIGHT)
        if isinstance(lights, list):
            # [{"idx": 1, "value": 0-255}, ...]
            levels = {
                int(light["idx"]): int(light.get("value") or 0)
                for light in lights
                if isinstance(light, dict) and isinstance(light.get("idx"), int)
            }
            state.fill_light_levels = levels
            level = round(max(levels.values(), default=0) * 100 / REST_BRIGHTNESS_MAX)
            state.fill_light_a = level
            state.fill_light_b = level
        ir = replies.get(REST_PATH_IR_LED)
        if isinstance(ir, dict):
            # ir1 = Aimed measure (laser head), ir2 = Quick measure (back right)
            if "ir1" in ir:
                state.ir_led_close = str(ir["ir1"]).lower() == "on"
            if "ir2" in ir:
                state.ir_led_global = str(ir["ir2"]).lower() == "on"

    async def _read_config(self, state: XtoolDeviceState) -> None:
        """Studio's Device settings."""
        config = await self._post_json(
            "/config/get", data={"alias": "config", "type": "user", "kv": list(P2_CONFIG_KEYS)},
        )
        if isinstance(config, dict):
            self._apply_config(state, config)
            await self._follow_studio_brightness(state, config.get("fillLightBrightness"))

    async def _read_head_position(self, state: XtoolDeviceState) -> None:
        position = await self.read_head_position()
        if position is not None:
            state.position_x, state.position_y = position

    async def read_head_position(self) -> tuple[float, float] | None:
        """The laser head's position now, in mm from home."""
        coord = await self._post_json(
            REST_PATH_LASER_HEAD, data={"action": PERIPHERAL_ACTION_GET_COORD, "waitTime": 0},
        )
        if not isinstance(coord, dict):
            return None
        x, y = coord.get("x"), coord.get("y")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y)):
            return None
        return float(x), float(y)

    def measure_offset(self, state: XtoolDeviceState) -> dict[str, float]:
        """The IR sensor's offset from the laser and its reading at thickness 0.

        From the P2's own ``laserMeasureOffset`` setting, such as
        "X64.3Y0.9Z136.7": x and y in mm, z the distance read on the slats.
        """
        text = str(state.device_config.get("laserMeasureOffset") or "")
        found = {
            axis.lower(): float(value)
            for axis, value in re.findall(r"([XYZ])(-?\d+(?:\.\d+)?)", text.upper())
        }
        return found if {"x", "y", "z"} <= found.keys() else {}

    async def measure_material(self) -> dict[str, float] | None:
        """One IR reading and the head position it was taken at.

        The reply's ``distance`` is the material's thickness: the reading at
        thickness 0 (``laserMeasureOffset`` Z) minus what the sensor read
        (``/peripheral/quest/ir_measure_distance`` gives the sensor's own
        distance). The sensor reads only while the close red dot (``ir_led``
        index 1) is on; otherwise the thickness is always 0.
        """
        result = await self._post_json(
            REST_PATH_IR_DISTANCE,
            data={"action": PERIPHERAL_ACTION_GET_DISTANCE, "type": "single"},
        )
        if not isinstance(result, dict):
            return None
        values: dict[str, float] = {}
        for key in ("distance", "coord_x", "coord_y"):
            value = result.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[key] = float(value)
        return values if "distance" in values else None

    async def move_laser_head(self, x: float, y: float, wait: int = 0) -> None:
        """Move the head to (x, y) mm from home; Studio's speed and wait."""
        await self._post(
            REST_PATH_LASER_HEAD,
            data={"action": PERIPHERAL_ACTION_GO_TO, "x": x, "y": y,
                  "f": P2_HEAD_SPEED, "waitTime": wait or P2_HEAD_WAIT_MS},
            timeout=P2_HEAD_WAIT_MS / 1000 + 5,
        )

    async def _read_slow(self, state: XtoolDeviceState) -> None:
        """Values that change rarely."""
        for path, attr in (
            ("/peripheral/cooling_fan?action=get", "cooling_fan_running"),
            ("/peripheral/smoking_fan?action=get", "smoking_fan_running"),
            ("/peripheral/water_pump?action=get", "water_pump_running"),
        ):
            reply = await self._get_json(path)
            if isinstance(reply, dict) and str(reply.get("state", "")).lower() in ("on", "off"):
                setattr(state, attr, str(reply["state"]).lower() == "on")
        value = _peri_float(await self._get_json("/peripheral/water_tmp?action=get"))
        if value is not None:
            state.water_temperature = value
        value = _peri_float(await self._get_json("/peripheral/water_flow?action=get"))
        if value is not None:
            state.water_flow = value
        work = await self._get_json("/device/workingInfo")
        if isinstance(work, dict):
            # Studio's Work info: Working time, Operating times, Standby time
            try:
                state.working_seconds = int(work["timeModeWorking"])
                state.session_count = int(work["numOfflineWorking"]) + int(work["numOnlineWorking"])
                state.standby_seconds = int(work["timeSystemWork"])
            except (KeyError, TypeError, ValueError):
                pass

    async def _follow_studio_brightness(self, state: XtoolDeviceState, setting: Any) -> None:
        """When Studio's Fill light brightness changes, the right and left lights take it."""
        if not isinstance(setting, int) or isinstance(setting, bool):
            return
        previous, self._studio_brightness = self._studio_brightness, setting
        if previous is None or setting == previous:
            return
        for index in (2, 3):
            if state.fill_light_levels.get(index) == setting:
                continue
            try:
                await self.set_fill_light_level(index, setting)
            except ConnectionError as err:
                _LOGGER.debug("%s", err)
                continue
            state.fill_light_levels = {**state.fill_light_levels, index: setting}

    @staticmethod
    def _apply_config(state: XtoolDeviceState, config: dict[str, Any]) -> None:
        state.device_config = {**state.device_config, **config}
        if isinstance(config.get("beepEnable"), bool):
            state.beep_enabled = config["beepEnable"]
        if isinstance(config.get("drawerCheck"), bool):
            state.drawer_check = config["drawerCheck"]
        if isinstance(config.get("flameLevelHLSelect"), int):
            state.flame_level_hl = config["flameLevelHLSelect"]
        if isinstance(config.get("flameAlarm"), bool):
            # The integration's flame_alarm scale: 0 = high, 1 = low, 2 = off
            if not config["flameAlarm"]:
                state.flame_alarm = 2
            else:
                state.flame_alarm = 0 if config.get("flameAlarmLevel1") else 1

    # --- Identity ---------------------------------------------------------

    async def get_device_info(self) -> DeviceInfo:
        """``/device/machineInfo``, keeping the whole reply for the device page."""
        data = await self._get_json(self.PATH_DEVICE_INFO)
        if not isinstance(data, dict):
            return DeviceInfo()
        self.machine_info = data
        return self._parse_machine_info_payload(data)

    def addresses(self) -> list[str]:
        """The laser's own addresses on the network (Wi-Fi and Ethernet)."""
        ips = self.machine_info.get("ip") if isinstance(self.machine_info, dict) else None
        if not isinstance(ips, dict):
            return []
        found = []
        for key, ip in ips.items():
            ip = str(ip or "")
            # usb0 is the USB link to a computer; 169.254 is an unplugged cable
            if not ip or key.startswith("usb") or ip.startswith(("169.254.", "127.")):
                continue
            found.append(ip)
        return found

    def ethernet_address(self) -> str:
        """The Ethernet address, or "" while no cable is connected."""
        ips = self.machine_info.get("ip") if isinstance(self.machine_info, dict) else None
        ip = str(ips.get("eth0-ip") or "") if isinstance(ips, dict) else ""
        # eth0's 169.254 address is there without a cable
        return "" if ip.startswith("169.254.") else ip

    def connection(self) -> str | None:
        """How Home Assistant reaches the laser: "ethernet" or "wifi"."""
        ips = self.machine_info.get("ip") if isinstance(self.machine_info, dict) else None
        if not isinstance(ips, dict):
            return None
        if self.host == ips.get("eth0-ip"):
            return "ethernet"
        if self.host == ips.get("wlan0-ip"):
            return "wifi"
        return None

    def model_variant(self) -> str:
        """Studio's Laser safety classification (the P2 reports no SKU)."""
        sub_type = str(self.machine_info.get("machineSubType") or "")
        return P2_SAFETY_CLASSES.get(sub_type, "")

    # --- Writes, as Studio sends them ------------------------------------

    async def set_config(self, **kv: Any) -> bool:
        """Write settings by Studio's keys (``/config/set``)."""
        reply = await self._post_json(
            "/config/set", data={"alias": "config", "type": "user", "kv": kv},
        )
        ok = reply is not None and not (isinstance(reply, dict) and reply.get("code") not in (None, 0))
        if not ok:
            _LOGGER.debug("P2 /config/set %s refused: %r", kv, reply)
        elif isinstance(kv.get("fillLightBrightness"), int):
            # Our own change, so the lights do not follow it as Studio's
            self._studio_brightness = kv["fillLightBrightness"]
        return ok

    async def set_fill_light_level(self, index: int, value: int) -> None:
        """One fill light's brightness, 0-255 (index 0 = all of them, as Studio uses)."""
        reply = await self._post_json(
            REST_PATH_FILL_LIGHT,
            data={"action": "set_bri", "idx": int(index), "value": max(0, min(REST_BRIGHTNESS_MAX, int(value)))},
        )
        # Success is {"code": 0, "data": <value>} (read here as the value);
        # a refusal keeps its code
        if reply is None or (isinstance(reply, dict) and reply.get("code") not in (None, 0)):
            raise ConnectionError(f"P2 fill light {index} not set: {reply!r}")

    async def set_beep_enabled(self, on: bool) -> None:
        """Buzzer (Studio writes ``beepEnable``; ``/setBeepEnable`` is refused)."""
        await self.set_config(beepEnable=bool(on))

    # --- Cameras -----------------------------------------------------------

    # Studio: stream 0 is the overview camera, 1 the close camera
    CAMERA_STREAMS = {"overview": 0, "closeup": 1}

    async def camera_snap(self, camera_name: str) -> bytes | None:
        """A photo (``/camera/snap`` on port 8329). A photo wakes a sleeping P2."""
        stream = self.CAMERA_STREAMS[camera_name]
        url = f"http://{self.host}:{REST_CAMERA_PORT}/camera/snap?stream={stream}"
        async with self._session().get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                raise ConnectionError(f"P2 {camera_name} photo: HTTP {resp.status}")
            return await resp.read()

    async def camera_calibration_file(self, file_name: str) -> bytes | None:
        """A camera's calibration, as Studio downloads it (``/file?action=download``)."""
        text = await self._request(
            "GET", f"/file?action=download&filename={file_name}", timeout=30, as_json=False,
        )
        # A missing file reads {"result":"failed"}
        if not text or '"grid_num_w"' not in text:
            return None
        return text.encode()

    # --- HTTP: one session for every request ---------------------------

    def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def _request(
        self, method: str, path: str, data: Any = None, timeout: float = 5.0, as_json: bool = True,
    ) -> Any:
        if self._is_offline():
            return None
        url = f"{self._base_url}{path}"
        try:
            async with self._session().request(
                method, url, json=data, timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                self._offline_until = 0.0
                if resp.status != 200:
                    _LOGGER.debug("P2 %s %s -> HTTP %d", method, url, resp.status)
                    return None
                text = await resp.text()
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError) as err:
            self._mark_offline(url, err)
            return None
        except Exception as err:
            _LOGGER.debug("P2 %s %s failed: %s", method, url, err)
            return None
        _LOGGER.debug("P2 %s %s body=%r -> %r", method, url, data, text[:500])
        if not as_json:
            return text
        try:
            result = json.loads(text)
        except ValueError:
            return None
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return result

    async def _get(self, path: str, timeout: float = 5.0) -> str | None:
        return await self._request("GET", path, timeout=timeout, as_json=False)

    async def _get_json(self, path: str, timeout: float = 5.0) -> Any:
        return await self._request("GET", path, timeout=timeout)

    async def _post(self, path: str, data: Any = None, timeout: float = 5.0) -> str | None:
        return await self._request("POST", path, data, timeout, as_json=False)

    async def _post_json(self, path: str, data: Any = None, timeout: float = 5.0) -> Any:
        return await self._request("POST", path, data, timeout)
