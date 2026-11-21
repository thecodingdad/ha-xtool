"""WS-V2 protocol dialect for the xTool X1 (JS001).

The X1 is a 55 W CO2 gantry with a flying galvo head, swappable laser
modules (diode galvo, a fiber / MOPA / UV backpack, an inkjet module),
three cameras and an optional conveyor feeder. It speaks xTool's
platform 2.0 interface like the M2: ``REPORT`` pushes,
``/v1/platform/device/*`` (machine-info, state/sync, flat config), the
process statistics, the camera snap and ``/v1/project/device/control``
for jobs. Those come from :class:`M2WSV2Protocol`; the X1's own
hardware is read here, as Studio's X1 extension (``xcs-ext-js001``)
reads it.

Event-like pushes arrive as frames whose ``data`` is a list of
``{name, level, info, content}`` notices; the job lifecycle comes from
the ``/v1/platform/device/process`` notices in them.

Checked on an X1 with firmware 40.140.003.01.ht18.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from ...const import XtoolStatus
from ..base import FirmwareFile, XtoolDeviceState
from .protocol import (
    WSV2_FILE_DEFAULT_PACKET,
    WSV2_FILE_DIGEST_MD5,
    WSV2_FILE_OP_DATA,
    WSV2_FILE_OP_REQUEST,
    WSV2_PATH,
    WSV2_PROTOCOL_FILE_TRANSFER,
    WSV2_PROTOCOL_JSON,
    _CLIENT_SESSION_ID,
    _decode_frames,
    _encode_frame,
    _ssl_context,
)
from .protocol_m2 import (
    M2_PATH_CONFIG,
    M2_PATH_COORDINATE,
    M2_PATH_DEVICE_CONTROL,
    M2_PATH_PROCESS_EVENT,
    M2_PATH_STATE,
    M2_PATH_STATE_SYNC,
    M2WSV2Protocol,
)

_LOGGER = logging.getLogger(__name__)

X1_PATH_LID = "/v1/project/peripheral/lid"
X1_PATH_LID_STATUS = "/v1/project/peripheral/lid-status"
X1_PATH_USB_KEY = "/v1/project/peripheral/usbkey"
X1_PATH_FILL_LIGHT = "/v1/project/peripheral/fill-light"
X1_PATH_FEEDER_INFO = "/v1/project/feeder/info"
X1_PATH_FEEDER_STATUS = "/v1/project/peripheral/feeder-status"
X1_PATH_FEEDER_SENSOR = "/v1/project/device/feeder-sensor"  # push only
X1_PATH_GALVO_EXIST = "/v1/project/peripheral/galvo-exist"
X1_PATH_LASER_BAG = "/v1/project/peripheral/laserbag-info"
X1_PATH_PRINTHEAD = "/v1/project/printhead/printhead-status"
X1_PATH_WIFI = "/v1/platform/wifi/connected-info"
X1_PATH_ALARM_RECORDS = "/v1/project/alarm/records"
X1_PATH_ALARM = "/v1/platform/device/alarm"  # notice only
X1_PATH_ACCESSORIES = "/v1/platform/accessories/list"
X1_PATH_ACCESSORY_CONTROL = "/v1/platform/accessories/control"
X1_PATH_ACCESSORY_MESSAGE = "/v1/project/accessory/message"  # push only

# Studio's X1 alarm code for a fire (``nd.FIRE``)
X1_ALARM_FIRE = 1001

X1_PATH_FILE_UPLOAD = "/v1/filetransfer/upload"
X1_PATH_FILE_FINISH = "/v1/filetransfer/finish"

# Studio's FileUploader: no progress for 120 s, or 30 min in all, ends an
# upload; it pings the file socket every 5 s (FILE_TRANSFER payload 2).
UPLOAD_STALL_SECONDS = 120.0
UPLOAD_TOTAL_SECONDS = 1800.0
UPLOAD_PING_SECONDS = 5.0
_UPLOAD_PING = b"\x02"
# Studio's FileTransferErrorMap for the finish codes
UPLOAD_ERRORS = {
    -1: "channel occupied", -2: "receive timeout", -3: "no space",
    -4: "file type not supported", -5: "digest type not supported",
    -6: "file in use", -7: "file not found", -8: "MD5 check failed", -9: "aborted",
}


class UploadFailed(RuntimeError):
    """The X1 ended a file upload with an error code."""


X1_PATH_DEVICE_UPGRADE = "/v1/platform/device/upgrade"
# Studio's X1 firmware upload: file type 2, always as package.zip
X1_FIRMWARE_FILE_TYPE = 2
X1_FIRMWARE_FILE_NAME = "package.zip"
# Studio's waits after starting the update: up to 10 min for the X1 to
# go away (it restarts to install), reconnecting every 3 s, then up to
# 5 min for it to report Idle.
RESTART_DOWN_SECONDS = 600.0
RESTART_UP_SECONDS = 600.0
RESTART_IDLE_SECONDS = 300.0
RECONNECT_SECONDS = 3.0

# Every setting Studio's X1 extension reads (its ``getConfigs`` key list).
X1_SETTINGS = (
    "smokeFanTimeout", "smokeFanSpeed", "beepEnable", "distCircleU",
    "feederRadius", "automationId", "dirtyCheckEnable", "dirtyCheckTime",
    "dirtyUncleanTotalTime", "dirtyLowFrequencyCheckEnable",
    "dirtyLowFrequencyCheckTime", "dirtyLowFrequencyUncleanTotalTime",
    "mdMode", "automationEnable", "laserDotCo2OffsetX", "laserDotCo2OffsetY",
    "laserBlueFramePower", "flameSensitivity", "hostCustomData",
    "laserDotSelfCheckTime", "focusDetectControlEnable", "feederYScan",
    "curGalvoLensType",
    "vibrationCalibDurationGalvoInkjet", "vibrationCalibDurationGalvoNoInkjet",
    "vibrationCalibDurationNoGalvoInkjet", "vibrationCalibDurationNoGalvoNoInkjet",
    "vibrationCalibBaseGalvoInkjet", "vibrationCalibBaseGalvoNoInkjet",
    "vibrationCalibBaseNoGalvoInkjet", "vibrationCalibBaseNoGalvoNoInkjet",
)
# The settings with an entity, read with a key list as Studio does (the
# X1 answers ``in_data is not array`` to a read without one).
X1_CONFIG_KEYS = ["smokeFanTimeout", "smokeFanSpeed", "beepEnable", "flameSensitivity"]
_X1_CONFIG_FIELDS = (
    ("smokeFanTimeout", "smoking_fan_duration"),
    ("smokeFanSpeed", "exhaust_fan_speed"),
    ("beepEnable", "beep_enabled_v2"),
    ("flameSensitivity", "flame_sensitivity"),
)
# The entities write the F family's names for the shared settings.
_X1_CONFIG_KEY_NAMES = {"purifierTimeout": "smokeFanTimeout"}

# Studio's X1 mode enum. ``SecureConfirm`` is reported while the laser
# waits for its button (the pairing confirmation); Studio counts it as
# not busy, with Idle (``isDeviceBusy``).
X1_MODE_MAP: dict[str, XtoolStatus] = {
    "Initial": XtoolStatus.INITIALIZING,
    "Idle": XtoolStatus.IDLE,
    "SecureConfirm": XtoolStatus.IDLE,
    "Busy": XtoolStatus.WORKING_API,
    "Measure": XtoolStatus.MEASURING,
    "Leveling": XtoolStatus.MEASURING,
    "Calibration": XtoolStatus.MEASURING,
    "Autowork": XtoolStatus.PROCESSING,
    "WorkReady": XtoolStatus.PROCESSING_READY,
    "WorkPlay": XtoolStatus.PROCESSING,
    "WorkPause": XtoolStatus.PAUSED,
    "Sleep": XtoolStatus.SLEEPING,
    "Upgrade": XtoolStatus.FIRMWARE_UPDATE,
    "Network": XtoolStatus.WIFI_SETUP,
    "Error": XtoolStatus.ERROR_LIMIT,
}

# Studio's pausePrint / resumePrint / cancelPrint.
_X1_JOB_ACTIONS: dict[str, str] = {
    "start": "RESUME",
    "pause": "PAUSE",
    "stop": "CANCEL",
}

# Job events from the process notices: Studio's process actions (its
# ``Xg`` enum) start, cancel and finish. Pause and resume follow from
# the state push.
_X1_PROCESS_EVENTS: dict[str, str] = {
    "start": "started",
    "cancel": "cancelled",
    "finish": "finished",
}

# Laser backpack ``type`` → sensor state (Studio's badges B1-B4: 5 W UV,
# 30 W fiber, 60 W MOPA, 5 W UV + 60 W MOPA).
_X1_BACKPACKS: dict[str, str] = {
    "RED_30W": "fiber_30w",
    "RED_60W": "mopa_60w",
    "UV": "uv_5w",
    "UV_RED_60W": "uv_mopa",
}

# The fill lights Studio sets, and the state fields that hold them.
_X1_FILL_LIGHT_FIELDS = {
    "left": "fill_light_left",
    "right": "fill_light_right",
    "near": "fill_light_near",
}


def _flag(value: Any) -> bool:
    """A yes/no the way Studio's X1 extension reads it (``"true"``, ``"1"``, true)."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return bool(value)


class X1WSV2Protocol(M2WSV2Protocol):
    """The X1: the platform 2.0 interface with the X1's own hardware."""

    _LATEST_STATE_FIELDS = M2WSV2Protocol._LATEST_STATE_FIELDS + (
        "fill_light_left", "fill_light_right", "fill_light_near",
        "feeder_installed", "feeder_front_blocked", "feeder_rear_blocked",
        "diode_laser_installed", "laser_backpack", "inkjet_module_installed",
        "exhaust_fan_speed", "flame_sensitivity",
    )

    # Studio's X1 config key list: no ``gapCheck`` (Cover check).
    SETTINGS = frozenset(X1_SETTINGS)

    # Studio updates the X1 with its own requests (``flash_firmware``
    # below), not the F family's ``/v1/device/upgrade-mode``.
    FIRMWARE_INSTALL = True

    # Settings, modules and statistics change rarely: every sixth poll.
    _SLOW_POLL_EVERY = 6

    def __init__(self, host: str, port: int | None = None) -> None:
        super().__init__(host, port)
        self._x1_poll_counter = 0
        # (accessory id, M-code report) from the accessory message pushes,
        # until the coordinator applies them
        self.pending_accessory_messages: list[tuple[int, str]] = []
        # The running uploads by channel, each with the finish it waits for
        self._upload_finish: dict[int, asyncio.Future[dict[str, Any]]] = {}

    # --- Polling -----------------------------------------------------------

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Status, lid, safety key and position every poll; the rest every sixth."""
        if not self._connected:
            await self.connect()

        # The reply is empty: the sync makes the laser push its state.
        try:
            await self.request(M2_PATH_STATE_SYNC, method="POST")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("X1 %s failed: %s", M2_PATH_STATE_SYNC, err)

        lid = await self._get_dict(X1_PATH_LID_STATUS)
        if isinstance(lid.get("lidStatus"), bool):
            self._latest["cover_open"] = lid["lidStatus"]
        key = await self._get_dict(X1_PATH_USB_KEY)
        if isinstance(key.get("usbKey"), bool):
            self._latest["machine_lock"] = key["usbKey"]
        coordinate = await self._get_dict(M2_PATH_COORDINATE)
        for axis in ("x", "y"):
            if isinstance(coordinate.get(axis), (int, float)):
                self._latest[f"position_{axis}"] = float(coordinate[axis])

        if self._x1_poll_counter % self._SLOW_POLL_EVERY == 0:
            await self._poll_slow(state)
        self._x1_poll_counter += 1

        self._apply_latest_to_state(state)

    async def _poll_slow(self, state: XtoolDeviceState) -> None:
        config = await self._get_dict(M2_PATH_CONFIG, data={"kv": X1_CONFIG_KEYS})
        self._apply_x1_configs(config)
        await self._poll_m2_statistics(state)
        self._apply_fill_lights(await self._get_dict(X1_PATH_FILL_LIGHT))

        feeder = await self._get_dict(X1_PATH_FEEDER_INFO)
        if "exist" in feeder:
            self._latest["feeder_installed"] = _flag(feeder["exist"])
        sensors = await self._get_dict(X1_PATH_FEEDER_STATUS)
        self._apply_feeder_sensors(sensors)

        galvo = await self._get_dict(X1_PATH_GALVO_EXIST)
        if "exist" in galvo:
            self._latest["diode_laser_installed"] = _flag(galvo["exist"])
        backpack = await self._get_dict(X1_PATH_LASER_BAG)
        if "exist" in backpack:
            self._latest["laser_backpack"] = (
                _X1_BACKPACKS.get(str(backpack.get("type")))
                if _flag(backpack["exist"]) else "none"
            )
        printhead = await self._get_dict(X1_PATH_PRINTHEAD)
        if "printhead_exist" in printhead:
            self._latest["inkjet_module_installed"] = _flag(printhead["printhead_exist"])

        wifi = await self._get_dict(X1_PATH_WIFI)
        if isinstance(wifi.get("rssi_dbm"), int):
            self._latest["wifi_signal"] = wifi["rssi_dbm"]

        # Studio's getAlarmRecords: the alarms still active, then the
        # history. The X1 answers {} when there are none.
        alarms = await self._get_dict(X1_PATH_ALARM_RECORDS, timeout=10.0)
        active = alarms.get("alarms-list")
        self._latest["alarm_present"] = bool(active) if isinstance(active, list) else False

    async def _get_dict(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            reply = await self.request(url, "GET", **kwargs)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("X1 %s failed: %s", url, err)
            return {}
        return reply if isinstance(reply, dict) else {}

    def _apply_x1_configs(self, config: dict[str, Any]) -> None:
        for key, field in _X1_CONFIG_FIELDS:
            if key in config:
                self._latest[field] = config[key]

    def _apply_fill_lights(self, reply: dict[str, Any]) -> None:
        """``{count, fillLights: [{name, brightness}]}``, read or pushed."""
        lights = reply.get("fillLights")
        if not isinstance(lights, list):
            return
        for light in lights:
            if not isinstance(light, dict):
                continue
            field = _X1_FILL_LIGHT_FIELDS.get(str(light.get("name")))
            if field is not None and isinstance(light.get("brightness"), int):
                self._latest[field] = light["brightness"]

    def _apply_feeder_sensors(self, reply: dict[str, Any]) -> None:
        """``block`` or ``unblock``: ``frontStatus`` / ``backStatus`` when
        read, ``frontSensor`` / ``backSensor`` when pushed."""
        for keys, field in (
            (("frontStatus", "frontSensor"), "feeder_front_blocked"),
            (("backStatus", "backSensor"), "feeder_rear_blocked"),
        ):
            for key in keys:
                if isinstance(reply.get(key), str):
                    self._latest[field] = reply[key] == "block"

    # --- Accessories ---------------------------------------------------------

    async def platform_accessories(self) -> list[dict[str, Any]]:
        """The laser's accessory list: ``[{dev_id, status, version, …}]``.

        ``status`` is true for the connected ones, Bluetooth and wired.
        """
        reply = await self.request(X1_PATH_ACCESSORIES, "GET", timeout=10.0)
        return [entry for entry in reply if isinstance(entry, dict)] if isinstance(reply, list) else []

    async def accessory_command(self, accessory_id: int, mcode: str) -> str | None:
        """Pass an M-code to an accessory, as Studio's platform 2.0 path does.

        Returns the reply without its leading M-code (``M9033 A40…`` →
        ``A40…``), or None when there is none.
        """
        reply = await self.request(
            X1_PATH_ACCESSORY_CONTROL, "POST",
            params={"id": accessory_id}, data={"command": mcode}, timeout=10.0,
        )
        result = reply.get("result") if isinstance(reply, dict) else None
        if not isinstance(result, str) or not result.strip():
            return None
        return result.strip().removeprefix(mcode.split(" ", 1)[0]).strip() or None

    # --- File upload -------------------------------------------------------

    async def upload_file(
        self, data: bytes, file_type: int, filename: str,
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """Send a file the way Studio's FileUploader does: the X1 pulls it.

        The ``file_stream`` socket opens first; ``PUT /v1/filetransfer/upload``
        announces the size and MD5 on a channel; the X1 asks for windows
        (FILE_REQUEST: channel, 5-byte offset, 3-byte size) and gets
        FILE_DATA packets (0x81, channel, 5-byte offset, data) of at most
        the packet size; it ends with ``/v1/filetransfer/finish``
        ``{code, channel}`` on either socket, 0 when the MD5 matched.
        Raises :class:`UploadFailed` with Studio's name for the code.
        """
        if not self._connected or self._session is None:
            await self.connect()
        # Channel 0 is never used (Studio's channelCounter)
        channel = self._next_file_channel() or self._next_file_channel()
        finish: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._upload_finish[channel] = finish
        url = (
            f"wss://{self.host}:{self._port}{WSV2_PATH}"
            f"?id={_CLIENT_SESSION_ID}&function=file_stream"
        )
        try:
            async with self._session.ws_connect(
                url, ssl=_ssl_context(), timeout=aiohttp.ClientTimeout(total=30),
                heartbeat=None, max_msg_size=0,
            ) as ws:
                reply = await self.request(X1_PATH_FILE_UPLOAD, "PUT", data={
                    "filetype": file_type, "filename": filename, "filesize": len(data),
                    "digesttype": WSV2_FILE_DIGEST_MD5,
                    "digestdata": hashlib.md5(data).hexdigest(),
                    "channel": channel, "packetsize": WSV2_FILE_DEFAULT_PACKET,
                })
                packet = WSV2_FILE_DEFAULT_PACKET
                if isinstance(reply, dict) and isinstance(reply.get("packetsize"), int):
                    packet = min(packet, reply["packetsize"])
                serving = asyncio.ensure_future(
                    self._serve_upload(ws, data, channel, packet, finish, progress_cb),
                )
                pinging = asyncio.ensure_future(self._ping_upload(ws))
                try:
                    result = await asyncio.wait_for(asyncio.shield(finish), UPLOAD_TOTAL_SECONDS)
                except TimeoutError:
                    with contextlib.suppress(Exception):
                        await self.request(X1_PATH_FILE_FINISH, "PUT", data={
                            "code": -9, "message": "File transfer aborted", "channel": channel,
                        })
                    raise
                finally:
                    serving.cancel()
                    pinging.cancel()
        finally:
            self._upload_finish.pop(channel, None)
        code = result.get("code")
        if code != 0:
            raise UploadFailed(f"{filename}: {UPLOAD_ERRORS.get(code, code)}")

    async def _serve_upload(
        self, ws: Any, data: bytes, channel: int, packet: int,
        finish: asyncio.Future[dict[str, Any]],
        progress_cb: Callable[[float], None] | None,
    ) -> None:
        """Answer the X1's FILE_REQUESTs; a stall ends the upload."""
        buffer = b""
        while not finish.done():
            try:
                message = await asyncio.wait_for(ws.receive(), UPLOAD_STALL_SECONDS)
            except TimeoutError:
                finish.set_result({"code": -2, "channel": channel})
                return
            if message.type != aiohttp.WSMsgType.BINARY:
                if not finish.done():
                    finish.set_exception(ConnectionError(f"file socket closed: {message.type}"))
                return
            frames, buffer = _decode_frames(buffer + message.data)
            for frame_type, payload in frames:
                if frame_type == WSV2_PROTOCOL_JSON:
                    self._note_upload_finish(payload)
                elif (
                    frame_type == WSV2_PROTOCOL_FILE_TRANSFER and len(payload) >= 10
                    and payload[0] == WSV2_FILE_OP_REQUEST and payload[1] == channel
                ):
                    offset = int.from_bytes(payload[2:7], "big")
                    end = min(len(data), offset + int.from_bytes(payload[7:10], "big"))
                    for start in range(offset, end, packet):
                        chunk = data[start:min(end, start + packet)]
                        await ws.send_bytes(_encode_frame(
                            bytes([WSV2_FILE_OP_DATA, channel]) + start.to_bytes(5, "big") + chunk,
                            protocol_type=WSV2_PROTOCOL_FILE_TRANSFER, cal_crc16=False,
                        ))
                        if progress_cb is not None:
                            progress_cb((start + len(chunk)) / max(len(data), 1))

    async def _ping_upload(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(UPLOAD_PING_SECONDS)
            await ws.send_bytes(_encode_frame(_UPLOAD_PING, protocol_type=WSV2_PROTOCOL_FILE_TRANSFER))

    def _note_upload_finish(self, payload: bytes | dict[str, Any]) -> None:
        """``{url: /v1/filetransfer/finish, data: {code, channel}}`` from the X1."""
        if isinstance(payload, (bytes, bytearray)):
            try:
                payload = json.loads(payload)
            except ValueError:
                return
        if not isinstance(payload, dict) or payload.get("url") != X1_PATH_FILE_FINISH:
            return
        result = payload.get("data")
        if isinstance(result, dict):
            finish = self._upload_finish.get(result.get("channel"))
            if finish is not None and not finish.done():
                finish.set_result(result)

    # --- Firmware ------------------------------------------------------------

    async def flash_firmware(
        self,
        files: list[FirmwareFile],
        blobs: list[bytes],
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """Install X1 firmware as Studio does (its ``uploadMainFirmware``).

        The package goes up as ``package.zip`` (file type 2), then ``POST
        /v1/platform/device/upgrade`` ``{filename, fileCheckType: 1,
        checksum: <md5>}`` starts the update. The X1 restarts to install
        it; this returns once it is back and Idle, so the update entity
        reads the new version.
        """
        if not files or len(files) != len(blobs):
            raise RuntimeError("X1 flash: no firmware package")

        def report(fraction: float) -> None:
            if progress_cb is not None:
                progress_cb(max(0.0, min(1.0, fraction)))

        for fw_file, blob in zip(files, blobs):
            if fw_file.md5 and hashlib.md5(blob).hexdigest() != fw_file.md5.lower():
                raise RuntimeError(f"X1 flash: {fw_file.name} does not match its MD5")
        share = 0.5 / len(files)
        for index, (fw_file, blob) in enumerate(zip(files, blobs)):
            await self.upload_file(
                blob, X1_FIRMWARE_FILE_TYPE, X1_FIRMWARE_FILE_NAME,
                lambda f, base=index * share: report(base + share * f),
            )
            reply = await self.request(X1_PATH_DEVICE_UPGRADE, "POST", data={
                "filename": X1_FIRMWARE_FILE_NAME, "fileCheckType": 1,
                "checksum": fw_file.md5 or hashlib.md5(blob).hexdigest(),
            }, timeout=120.0)
            _LOGGER.info("X1 firmware update started (%s): %r", fw_file.name, reply)
            report(0.55)
            await self._wait_for_restart(report)
        report(1.0)

    async def _wait_for_restart(self, report: Callable[[float], None]) -> None:
        """The X1 goes away to install, comes back, and reports Idle."""
        deadline = time.monotonic() + RESTART_DOWN_SECONDS
        while self.connected:
            if time.monotonic() > deadline:
                raise TimeoutError("the X1 did not restart to install the firmware")
            await asyncio.sleep(RECONNECT_SECONDS)
        # The mode from before the restart says nothing about after it
        self._latest.pop("status", None)
        report(0.65)
        deadline = time.monotonic() + RESTART_UP_SECONDS
        while True:
            try:
                await self.connect()
                break
            except Exception as err:  # noqa: BLE001
                if time.monotonic() > deadline:
                    raise TimeoutError("the X1 did not come back after the firmware update") from err
                await asyncio.sleep(RECONNECT_SECONDS)
        report(0.85)
        deadline = time.monotonic() + RESTART_IDLE_SECONDS
        while self._latest.get("status") != XtoolStatus.IDLE:
            if time.monotonic() > deadline:
                raise TimeoutError("the X1 did not report Idle after the firmware update")
            with contextlib.suppress(Exception):
                await self.request(M2_PATH_STATE_SYNC, method="POST")
            await asyncio.sleep(RECONNECT_SECONDS)

    # --- Writes ------------------------------------------------------------

    async def set_config(self, key: str, value: Any) -> dict[str, Any]:
        """PUT one setting as Studio does: a flat body, booleans as booleans."""
        key = _X1_CONFIG_KEY_NAMES.get(key, key)
        result = await self.request(M2_PATH_CONFIG, "PUT", data={key: value})
        # Kept as the laser's own: the settings are read every sixth poll.
        self._apply_x1_configs({key: value})
        return result

    async def set_named_fill_light(self, name: str, brightness: int) -> dict[str, Any]:
        """Studio's setFillLight: ``PUT fill-light?name=<name>`` with ``{brightness}``."""
        result = await self.request(
            X1_PATH_FILL_LIGHT, "PUT",
            params={"name": name}, data={"brightness": brightness},
        )
        self._latest[_X1_FILL_LIGHT_FIELDS[name]] = brightness
        return result

    async def set_processing_state(self, action: str) -> dict[str, Any]:
        return await self.request(
            M2_PATH_DEVICE_CONTROL,
            method="POST",
            params={"action": _X1_JOB_ACTIONS[action]},
        )

    async def set_peripheral(
        self, peripheral_type: str, action: str | None = None, **extra: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            f"X1 has no {peripheral_type!r} peripheral control"
        )

    # --- Pushes ------------------------------------------------------------

    def _dispatch_push(self, event: dict[str, Any]) -> None:
        """Route a push by its shape, as Studio's X1 extension does.

        A list holds event notices (``NOTIFY``); an object maps paths to
        their state (``REPORT``) or to an event's content. The method
        name does not matter to Studio.
        """
        if event.get("url") == X1_PATH_FILE_FINISH:
            self._note_upload_finish(event)
            return
        data = event.get("data")
        if isinstance(data, list):
            for notice in data:
                if isinstance(notice, dict):
                    self._dispatch_x1_notice(notice)
            return
        if isinstance(data, dict) and "url" not in event:
            for url, payload in data.items():
                if url in (M2_PATH_PROCESS_EVENT, X1_PATH_ALARM):
                    # Studio's handleEventFromObject
                    content = payload.get("content") if isinstance(payload, dict) else None
                    self._dispatch_x1_notice(
                        {"name": url, "content": content} if content else {"name": url, "info": payload},
                    )
                else:
                    self._dispatch_m2_report(url, payload)
            return
        super()._dispatch_push(event)

    def _dispatch_x1_notice(self, notice: dict[str, Any]) -> None:
        """One ``{name, level, info, content}`` entry of a NOTIFY frame."""
        name = notice.get("name")
        if name == X1_PATH_FEEDER_SENSOR and isinstance(notice.get("info"), dict):
            self._apply_feeder_sensors(notice["info"])
            return
        if name == X1_PATH_ALARM:
            self._dispatch_x1_alarms(notice.get("info"))
            return
        if name != M2_PATH_PROCESS_EVENT:
            _LOGGER.debug("X1 notice %s: %r", name, notice.get("info"))
            return
        # Studio's handleProcessingEvent reads ``content``, else ``info``.
        content = notice.get("content") or notice.get("info")
        if not isinstance(content, dict):
            return
        task_id = str(content.get("taskId") or self._latest.get("task_id") or "")
        if content.get("taskId"):
            self._latest["task_id"] = task_id
        kind = _X1_PROCESS_EVENTS.get(str(content.get("action")))
        if kind is not None:
            self._maybe_emit_job_event(
                kind, task_id, {"task_id": task_id} if task_id else None,
            )

    def _dispatch_x1_alarms(self, alarms: Any) -> None:
        """Studio's handleAlarmEvent: ``[{info: {code, message}, level}]``.

        The fire alarm (code 1001) fires the fire warning; the Alarm
        sensor follows the active alarm list, read every sixth poll.
        """
        if not isinstance(alarms, list):
            return
        for alarm in alarms:
            info = alarm.get("info") if isinstance(alarm, dict) else None
            if isinstance(info, dict) and str(info.get("code")) == str(X1_ALARM_FIRE):
                self._latest["alarm_present"] = True
                self._pending_events.append(
                    ("fire_warning", "triggered", {"level": alarm.get("level")}),
                )
                return

    def _dispatch_m2_report(self, url: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            super()._dispatch_m2_report(url, payload)
            return
        if url == M2_PATH_STATE:
            # {mode, moveState, gohomeState, taskId, processInfo, …}
            mode = payload.get("mode")
            if isinstance(mode, str) and mode in X1_MODE_MAP:
                self._latest["status"] = X1_MODE_MAP[mode]
            if payload.get("taskId"):
                self._latest["task_id"] = str(payload["taskId"])
        elif url == X1_PATH_LID:
            # Studio's lid push: ``state`` True = open
            if isinstance(payload.get("state"), bool):
                self._latest["cover_open"] = payload["state"]
        elif url == X1_PATH_USB_KEY:
            if isinstance(payload.get("state"), bool):
                self._latest["machine_lock"] = payload["state"]
        elif url == X1_PATH_FILL_LIGHT:
            # Only the light that changed: {count: 1, fillLights: [...]}
            self._apply_fill_lights(payload)
        elif url == X1_PATH_FEEDER_SENSOR:
            self._apply_feeder_sensors(payload)
        elif url == X1_PATH_ACCESSORY_MESSAGE:
            # {dev_id, message}: an accessory's own report, e.g. the Fire
            # Safety Set 2's M9033 about every 2 s
            if isinstance(payload.get("dev_id"), int) and isinstance(payload.get("message"), str):
                self.pending_accessory_messages.append((payload["dev_id"], payload["message"]))
        else:
            super()._dispatch_m2_report(url, payload)
