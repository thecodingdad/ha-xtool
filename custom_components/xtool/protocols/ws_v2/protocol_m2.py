"""WS-V2 protocol dialect for the xTool M2.

M2 (model_id JS002) is classified by Studio v1.7.23 as a V2-protocol
device (``protocolVersion:"V2"``, ``channelType:"socket"``) — it rides
the same TLS WebSocket transport on port 28900 and the same multi-
channel framework (instruction / file_stream / media_stream) as the
F1/F2 family. The wire surface diverges in URL set:

- ``/v1/platform/device/*`` namespace (machine-info, state,
  config, capabilities, alarm) replaces the F-family's
  ``/v1/device/machineInfo`` / ``runtime-infos`` / ``configs``
  / ``alarms`` / ``statistics`` umbrellas.
- ``/v1/project/*`` namespace (per-peripheral, per-tool, inkjet,
  measure, calibration, device control) replaces
  ``/v1/peripheral/param`` + ``/v1/cnc/*`` + ``/v1/laser-head/*``.
- Job control: ``POST /v1/project/device/control?action=START |
  PAUSE | RESUME | CANCEL`` (not the F-family's
  ``PUT /v1/processing/state?action=start | pause | stop``).
- Status sync: ``POST /v1/platform/device/state/sync`` (the bare
  ``/v1/platform/device/state`` URL is a push-event routing key
  only; an active snapshot pull uses the ``/state/sync`` POST).
- Camera snap: ``POST /v1/platform/camera/snap?name=far | near |
  side`` (not the F-family's GET ``/v1/camera/snap``).
- Configs: ``PUT /v1/platform/device/config`` with a **flat** body
  (``{key: value}``, no ``alias/type/kv`` envelope like the F
  family).
- Push events arrive as ``{method:"REPORT", data:{"<url>":<payload>,
  …}}`` — one REPORT frame can carry multiple URL→payload pairs
  instead of the F family's single ``{url:…, data:{module,type,info}}``
  shape.

The transport-layer plumbing (channels, CRC framing, transaction-id
correlation, file_stream, OTA) is unchanged from :class:`WSV2Protocol`
so this subclass overrides only the diverging URL-specific methods
plus the push-frame dispatcher.
"""

from __future__ import annotations

import logging
from typing import Any

from ...const import XtoolStatus
from ..base import DeviceInfo, LaserInfo, XtoolDeviceState
from .protocol import WSV2Protocol

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
M2_PATH_ABSOLUTE_MOVE = "/v1/project/control/absolute-move"
M2_PATH_MEASURE_EXECUTE = "/v1/project/measure/execute"
M2_PATH_RUNNING_STATUS = "/v1/project/running/status"
M2_PATH_LASER_HEAD_INFO = "/v1/project/laser-head/info"
M2_PATH_LASER_HEAD_TEMP = "/v1/project/laser-head/get-temp"
M2_PATH_NTC_TEMP = "/v1/project/ntc/temperature"
M2_PATH_PERIPHERAL_LID = "/v1/project/peripheral/lid"
M2_PATH_PERIPHERAL_PALLET = "/v1/project/peripheral/pallet"
M2_PATH_PERIPHERAL_FILL_LIGHT = "/v1/project/peripheral/fill-light"
M2_PATH_PERIPHERAL_AIR_PUMP = "/v1/project/peripheral/airPump-control"
M2_PATH_PERIPHERAL_SMOKE_FAN = "/v1/project/peripheral/smokeFan-control"

# Inkjet (read-only surface — actions cap/clean deferred).
M2_PATH_INKJET_INK_VOLUME = "/v1/project/inkjet/ink-volume"
M2_PATH_INKJET_CAP_STATUS = "/v1/project/inkjet/cap-status"
M2_PATH_INKJET_INK_STATUS = "/v1/project/inkjet/ink-status"
M2_PATH_INKJET_INFO = "/v1/project/inkjet/info"

# Job-control action labels carried in the ``params`` field.
M2_ACTION_START = "START"
M2_ACTION_PAUSE = "PAUSE"
M2_ACTION_RESUME = "RESUME"
M2_ACTION_CANCEL = "CANCEL"


# Mode-enum mapping — M2 firmware uses short-name mode strings
# (verified against Studio v1.7.23 JS002 bundle:
# ``e.IDLE=`Idle``, ``e.SLEEP=`Sleep``, ``e.WORK_PLAY=`WorkPlay``,
# …). Push frames on ``/v1/platform/device/state`` carry these
# exact strings — no ``.upper()`` normalisation, exact-match lookup.
# ``P_*`` legacy keys retained as aliases in case older firmware
# revisions still emit them from the ``/state/sync`` POST reply.
M2_MODE_MAP: dict[str, XtoolStatus] = {
    # M2 short-name enum (JS002 bundle constants).
    "Initial":    XtoolStatus.INITIALIZING,
    "Idle":       XtoolStatus.IDLE,
    "WorkReady":  XtoolStatus.PROCESSING_READY,
    "WorkPlay":   XtoolStatus.PROCESSING,
    "WorkPause":  XtoolStatus.PAUSED,
    "WorkCancel": XtoolStatus.CANCELLING,
    "WorkDone":   XtoolStatus.FINISHED,
    "finish":     XtoolStatus.FINISHED,   # bundle alias
    "Sleep":      XtoolStatus.SLEEPING,
    "Error":      XtoolStatus.ERROR_LIMIT,
    # Legacy P_* names (aliases — some older M2 firmwares may still
    # emit these on the ``/state/sync`` POST response).
    "P_IDLE":               XtoolStatus.IDLE,
    "P_FRAMING":            XtoolStatus.FRAMING,
    "P_FRAME_READY":        XtoolStatus.FRAME_READY,
    "P_PROCESSING_READY":   XtoolStatus.PROCESSING_READY,
    "P_PROCESSING":         XtoolStatus.PROCESSING,
    "P_PAUSE":              XtoolStatus.PAUSED,
    "P_FINISH":             XtoolStatus.FINISHED,
    "P_FINISH_PROCESSING":  XtoolStatus.FINISHED,
    "P_SLEEP":              XtoolStatus.SLEEPING,
    "P_INITIALIZING":       XtoolStatus.INITIALIZING,
    "P_ERROR":              XtoolStatus.ERROR_LIMIT,
    "P_EMERGENCY_STOP":     XtoolStatus.ERROR_LIMIT,
}


# Map ws_v2 base ``set_processing_state`` action verbs to M2's
# uppercase action labels. Used to translate the entity-side
# ``"pause" | "start" | "stop"`` strings into the M2 wire form.
_M2_JOB_ACTION_TABLE: dict[str, str] = {
    "start": M2_ACTION_START,   # used for both initial start and resume
    "pause": M2_ACTION_PAUSE,
    "stop": M2_ACTION_CANCEL,
}


class M2WSV2Protocol(WSV2Protocol):
    """WS-V2 protocol with the M2 (JS002) URL surface.

    Inherits transport, multi-channel handling, file_stream and OTA
    helpers from :class:`WSV2Protocol`. Overrides the URL-specific
    methods (`get_device_info`, `_poll_runtime_status`, `poll_state`,
    `set_config`, `set_peripheral`, `set_processing_state`,
    `parts_control`, `camera_snap`) plus the push-frame dispatcher
    (`_dispatch_push`) to handle M2's ``REPORT`` envelope.
    """

    PATH_DEVICE_INFO = M2_PATH_MACHINE_INFO
    PATH_CAMERA_SNAP = M2_PATH_CAMERA_SNAP

    # Slow-cadence poll counter for the inkjet block — inkjet
    # sensors don't need to refresh every tick; every 6 polls
    # (~30 s at 5 s base interval) matches the F-family's
    # config-blob cadence.
    _INKJET_POLL_EVERY = 6

    def __init__(self, host: str, port: int | None = None) -> None:
        # Match base signature (port has a default in the base).
        if port is None:
            super().__init__(host)
        else:
            super().__init__(host, port)
        self._m2_poll_counter = 0

    # --- Status (overrides base _poll_runtime_status) ----------------------

    async def _poll_runtime_status(self, state: XtoolDeviceState) -> None:
        """Pull the M2 status snapshot via ``state/sync`` POST.

        The bare ``/v1/platform/device/state`` URL is a push-event
        routing key only — Studio's ``syncDeviceState`` issues a POST
        to ``/state/sync`` for the active pull. Response payload is
        the same shape as the push: ``{curMode:{mode, desc, subMode,
        taskId}}``. Mode names are M2's short-form strings ("Idle",
        "Sleep", "WorkPlay", …) — exact-match against
        :data:`M2_MODE_MAP`, no case coercion.
        """
        try:
            s = await self.request(M2_PATH_STATE_SYNC, method="POST")
        except Exception as err:
            _LOGGER.debug("M2 %s failed: %s", M2_PATH_STATE_SYNC, err)
            s = {}
        if isinstance(s, dict):
            cur = s.get("curMode") or {}
            if isinstance(cur, dict):
                mode = cur.get("mode")
                if isinstance(mode, str) and mode in M2_MODE_MAP:
                    state.status = M2_MODE_MAP[mode]
                    self._latest["status"] = state.status
                task_id = cur.get("taskId")
                if task_id:
                    state.task_id = str(task_id)

    # --- Peripheral poll (replaces base /v1/peripheral/param loop) -------

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """M2 polling cycle.

        Reuses the base status step (overridden above) but reads M2-
        specific peripheral routes instead of ``/v1/peripheral/param``.
        Inkjet block runs on a slow cadence when ``model.has_inkjet``
        is set.
        """
        if not self._connected:
            await self.connect()

        # 1. Status (overridden — uses POST /state/sync, M2_MODE_MAP).
        await self._poll_runtime_status(state)

        # 2. Cover / lid via /v1/project/peripheral/lid GET. Response
        # is ``{state: <bool>}`` on current firmware
        # (40.141.010.01.ht03 verified via issue #7 log); older
        # revisions returned ``{state: "on"|"off"}`` so tolerate both.
        try:
            lid = await self.request(M2_PATH_PERIPHERAL_LID, "GET")
        except Exception:
            lid = {}
        if isinstance(lid, dict):
            lid_state = lid.get("state")
            if isinstance(lid_state, bool):
                state.cover_open = lid_state
            elif isinstance(lid_state, str):
                state.cover_open = lid_state == "on"

        # 3. Coordinate — laser-head X/Y/Z position.
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

        # 4. Inkjet block — slow cadence, gated on model.has_inkjet.
        model = getattr(self, "_model", None)
        if (
            model is not None
            and getattr(model, "has_inkjet", False)
            and self._m2_poll_counter % self._INKJET_POLL_EVERY == 0
        ):
            await self._poll_inkjet(state)
        self._m2_poll_counter += 1

        # Drain push-cached fields exactly like the base class does.
        self._apply_latest_to_state(state)

    async def _poll_inkjet(self, state: XtoolDeviceState) -> None:
        """Refresh the inkjet read-only surface on the M2.

        Endpoints (all GET, response shapes taken from Studio
        v1.7.23 JS002 bundle ``transformResult`` clauses):

        - ``/v1/project/inkjet/ink-volume`` → ``{C, M, Y, K}`` raw
          numeric levels per ink channel.
        - ``/v1/project/inkjet/cap-status`` → ``{status: 0|1}``
          (0=CLOSE, 1=OPEN per bundle enum ``d4``).
        - ``/v1/project/inkjet/ink-status`` → ``{status: 0|1}``
          (0=UNINSTALLED, 1=INSTALLED per bundle enum ``f4``).
        - ``/v1/project/inkjet/info`` → ``{Calibrated, SN, Version,
          TonerSN}`` identity + calibration blob.

        Any endpoint erroring (e.g. head not installed on this
        specific M2 hardware) leaves the corresponding state
        field at its previous value.
        """
        # Ink volumes (C/M/Y/K).
        try:
            vol = await self.request(M2_PATH_INKJET_INK_VOLUME, "GET")
        except Exception as err:
            _LOGGER.debug("M2 %s failed: %s", M2_PATH_INKJET_INK_VOLUME, err)
            vol = {}
        if isinstance(vol, dict):
            for src, dst in (
                ("C", "inkjet_ink_c"),
                ("M", "inkjet_ink_m"),
                ("Y", "inkjet_ink_y"),
                ("K", "inkjet_ink_k"),
            ):
                v = vol.get(src)
                if isinstance(v, (int, float)):
                    setattr(state, dst, int(v))

        # Cap status (head cover open/closed).
        try:
            cap = await self.request(M2_PATH_INKJET_CAP_STATUS, "GET")
        except Exception as err:
            _LOGGER.debug("M2 %s failed: %s", M2_PATH_INKJET_CAP_STATUS, err)
            cap = {}
        if isinstance(cap, dict):
            status = cap.get("status")
            if isinstance(status, (int, bool)):
                # 0 = CLOSE (capped), 1 = OPEN (uncapped). Entity
                # surface: ``inkjet_head_capped`` = True when
                # capped.
                state.inkjet_head_capped = int(status) == 0

        # Toner install status.
        try:
            tone = await self.request(M2_PATH_INKJET_INK_STATUS, "GET")
        except Exception as err:
            _LOGGER.debug("M2 %s failed: %s", M2_PATH_INKJET_INK_STATUS, err)
            tone = {}
        if isinstance(tone, dict):
            status = tone.get("status")
            if isinstance(status, (int, bool)):
                # 0 = UNINSTALLED, 1 = INSTALLED.
                state.inkjet_toner_installed = int(status) == 1

        # Identity + calibration.
        try:
            info = await self.request(M2_PATH_INKJET_INFO, "GET")
        except Exception as err:
            _LOGGER.debug("M2 %s failed: %s", M2_PATH_INKJET_INFO, err)
            info = {}
        if isinstance(info, dict):
            if info.get("SN") is not None:
                state.inkjet_sn = str(info.get("SN"))
            if info.get("Version") is not None:
                state.inkjet_version = str(info.get("Version"))
            if info.get("TonerSN") is not None:
                state.inkjet_toner_sn = str(info.get("TonerSN"))
            calibrated = info.get("Calibrated")
            if isinstance(calibrated, (int, bool)):
                state.inkjet_calibrated = bool(calibrated)

    # --- Device identity --------------------------------------------------

    async def get_device_info(self) -> DeviceInfo:
        """Read identity from ``/v1/platform/device/machine-info``.

        The response shape mirrors the F-family ``machineInfo`` blob
        (``deviceName``, ``sn``, ``mac``, ``firmware.package_version``,
        ``laserPower:[]``) so the base ``get_device_info`` mostly
        works — only the URL changes. We still override to handle
        firmware identification under a slightly different key shape
        and to cache fields into ``_latest``.
        """
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

    # --- Config writes ----------------------------------------------------

    async def set_config(self, key: str, value: Any) -> dict[str, Any]:
        """PUT a single config key to ``/v1/platform/device/config``.

        M2's config surface uses a **flat** body (``{key: value}``)
        rather than the F family's ``{alias:"config", type:"user",
        kv:{…}}`` envelope. Verified against Studio v1.7.23 bundle
        route ``setFanSmokeExhaustTime`` whose ``transformRequest``
        clause returns a bare ``{smokeFanTimeout:e}`` dict. Bool
        values are coerced to int 0/1 to match the F-family default
        (safe on M2 — its schema hasn't been observed rejecting
        int, unlike some F2 UV firmwares).
        """
        coerced: Any = value
        if isinstance(value, bool):
            coerced = 1 if value else 0
        return await self.request(
            M2_PATH_CONFIG,
            "PUT",
            data={key: coerced},
        )

    # --- Peripheral writes (replaces base /v1/peripheral/param dispatch) --

    async def set_peripheral(
        self,
        peripheral_type: str,
        action: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Dispatch a peripheral-write to M2's per-URL routes.

        M2 doesn't expose ``/v1/peripheral/param`` at all
        (``code 404: API not found``); every peripheral has its
        own ``/v1/project/*`` route with a route-specific body
        shape. This override maps the entity-layer
        ``(peripheral_type, action)`` calls to the matching
        M2 route.

        Unknown ``(peripheral_type, action)`` combinations raise
        :class:`NotImplementedError` so unmapped entities fail
        loud instead of falling through to a 404-loop on the base
        class's route.
        """
        # laser_head — homing + move_to
        if peripheral_type == "laser_head":
            if action == "home_all":
                return await self.request(
                    M2_PATH_CONTROL_HOME, "POST",
                    params={"axis": "ALL"},
                )
            if action == "home_xy":
                return await self.request(
                    M2_PATH_CONTROL_HOME, "POST",
                    params={"axis": "XY"},
                )
            if action == "home_z":
                return await self.request(
                    M2_PATH_CONTROL_HOME, "POST",
                    params={"axis": "Z"},
                )
            if action == "move_to":
                x = float(extra.get("x", 0))
                y = float(extra.get("y", 0))
                return await self.request(
                    M2_PATH_ABSOLUTE_MOVE, "POST",
                    data={"coor": {"x": x, "y": y}, "speed": 18000},
                )

        # ir_measure_distance — Z-axis distance probe
        if peripheral_type == "ir_measure_distance" and action == "measure":
            return await self.request(
                M2_PATH_MEASURE_EXECUTE, "POST",
                data={
                    "measurement": {"axis": "Z", "retPos": 0, "retSpeed": 0},
                    "finish": {"action": "NONE"},
                },
            )

        # fill_light — brightness (base entity surfaces a single
        # channel; map to M2's ``far`` = global overview light).
        if peripheral_type == "fill_light" and action == "set_brightness":
            value = int(extra.get("value", 0))
            return await self.request(
                M2_PATH_PERIPHERAL_FILL_LIGHT, "PUT",
                params={"name": "far"},
                data={"brightness": value},
            )

        # smoking_fan — on/off. Bundle only defines the URL
        # (``setSmokeFanSpeed``); body shape borrowed from the
        # ``weakLaser`` precedent (``{enable:!0}``) since Studio
        # doesn't ship an explicit ``transformRequest``.
        if peripheral_type == "smoking_fan" and action in ("on", "off"):
            return await self.request(
                M2_PATH_PERIPHERAL_SMOKE_FAN, "POST",
                data={"enable": action == "on"},
            )

        # air_pump — same best-effort body shape as smoking_fan.
        if peripheral_type == "air_pump" and action in ("on", "off"):
            return await self.request(
                M2_PATH_PERIPHERAL_AIR_PUMP, "POST",
                data={"enable": action == "on"},
            )

        raise NotImplementedError(
            f"M2 has no set_peripheral mapping for "
            f"peripheral_type={peripheral_type!r} action={action!r}"
        )

    # --- Job control ------------------------------------------------------

    async def set_processing_state(self, action: str) -> dict[str, Any]:
        """Dispatch job-control verbs to ``/v1/project/device/control``.

        The base ws_v2 entity surface calls
        ``set_processing_state("start" | "pause" | "stop")`` for
        job control — M2's wire surface routes those to ``POST
        /v1/project/device/control?action=START | PAUSE | CANCEL``
        instead of the F-family's
        ``PUT /v1/processing/state?action=…``. Resume is the same
        as start (re-issue ``action=start``) so this single override
        handles all three buttons; the entity layer doesn't need to
        know about M2's uppercase action labels.
        """
        m2_action = _M2_JOB_ACTION_TABLE.get(action)
        if m2_action is None:
            raise ValueError(f"M2 unknown processing action: {action}")
        return await self.request(
            M2_PATH_DEVICE_CONTROL,
            method="POST",
            params={"action": m2_action},
        )

    # --- Accessory passthrough (no-op on M2) ------------------------------

    async def parts_control(
        self, mcode: str, prefix: bytes, timeout: float = 6.0,
    ) -> str | None:
        """M2 doesn't expose ``/v1/parts/control``.

        Accessories on M2 live behind ``GET /v1/platform/accessories/list``
        — a completely different topology (no F0F7 tunnel, no per-mcode
        passthrough). Return ``None`` so the coordinator's
        ``_poll_accessories`` records "no accessories" instead of
        spamming ``code 404: API not found`` every poll cycle. Proper
        M2 accessory support lands in a follow-up once the
        ``/v1/platform/accessories/list`` payload is verified live.
        """
        return None

    # --- Push event dispatch ----------------------------------------------

    def _dispatch_push(self, event: dict[str, Any]) -> None:
        """Handle M2's ``REPORT`` push envelope + fall through to base.

        M2 push frames arrive as::

            {"type":"request", "method":"REPORT",
             "data": {"<url>": <payload>, ...}}

        where ``data`` is a dict keyed by URL routing key (each
        REPORT frame can carry multiple URL→payload pairs — e.g.
        machine-info + wifi info together). This differs from the
        F family's ``{url:"<path>", data:{module,type,info}}``
        shape.

        Iterate every ``(url, payload)`` pair and dispatch each to
        its M2-specific handler. Anything not a REPORT frame falls
        through to the base ``_dispatch_push``.
        """
        if event.get("method") == "REPORT" and isinstance(event.get("data"), dict):
            for url, payload in event["data"].items():
                self._dispatch_m2_report(url, payload)
            return
        super()._dispatch_push(event)

    def _dispatch_m2_report(self, url: str, payload: Any) -> None:
        """Dispatch a single M2 REPORT ``(url, payload)`` pair."""
        if url == M2_PATH_STATE:
            # /v1/platform/device/state → {mode: "<short-name>"}
            if isinstance(payload, dict):
                mode = payload.get("mode")
                if isinstance(mode, str) and mode in M2_MODE_MAP:
                    self._latest["status"] = M2_MODE_MAP[mode]
                task_id = payload.get("taskId")
                if task_id:
                    self._latest["task_id"] = str(task_id)
            return

        if url == M2_PATH_ALARM:
            # /v1/platform/device/alarm → list; empty = no alarm.
            self._latest["alarm_present"] = bool(payload)
            return

        if url == M2_PATH_MACHINE_INFO:
            # /v1/platform/device/machine-info → identity blob.
            if isinstance(payload, dict):
                if payload.get("deviceName"):
                    self._latest["device_name"] = str(payload["deviceName"])
                if payload.get("sn"):
                    self._latest["serial_number"] = str(payload["sn"])
                if payload.get("mac"):
                    self._latest["mac_address"] = str(payload["mac"])
                firmware = payload.get("firmware")
                if isinstance(firmware, dict):
                    fw_ver = (
                        firmware.get("package_version")
                        or firmware.get("version")
                    )
                    if fw_ver:
                        self._latest["firmware_version"] = str(fw_ver)
            return

        # /v1/platform/wifi/info + any other unhandled URL —
        # log-only for now. Wire the WiFi diagnostic sensors in a
        # follow-up once the entity surface lands.
        _LOGGER.debug("M2 REPORT unhandled url=%s payload=%r", url, payload)

    # --- Camera snap ------------------------------------------------------

    async def camera_snap(self, camera_name: str = "far") -> bytes | None:
        """Two-step snap: instruction POST returns filename, file_stream delivers blob.

        Verified against Studio v1.7.23 JS002 bundle. Studio's
        ``captureGlobalImage`` route only issues the POST — the
        actual JPEG blob is fetched via a companion
        ``downloadImage`` call flagged ``isFileTransfer:!0,
        responseType:"blob"``, which routes to the ``file_stream``
        WS descriptor ``{fileType:5, fileName:<uuid>}``.

        Same two-step shape as the F-family camera_snap, only
        difference is the instruction URL + POST/GET method — so
        we can reuse the inherited ``_download_file_stream``
        helper verbatim.
        """
        try:
            reply = await self.request(
                M2_PATH_CAMERA_SNAP,
                method="POST",
                params={"name": camera_name},
                timeout=15.0,
            )
        except Exception as err:
            _LOGGER.debug("M2 camera snap %s failed: %s", camera_name, err)
            return None
        if not isinstance(reply, dict):
            return None
        filename = reply.get("filename")
        if not isinstance(filename, str) or not filename:
            _LOGGER.debug(
                "M2 camera snap %s: no filename in reply %r",
                camera_name, reply,
            )
            return None
        try:
            return await self._download_file_stream(filename, file_type=5)
        except Exception as err:
            _LOGGER.debug(
                "M2 camera snap %s file_stream download failed: %s",
                camera_name, err,
            )
            return None

    # --- Firmware update --------------------------------------------------

    async def get_firmware_versions(self, coordinator: Any) -> dict[str, str]:
        """Single-package cloud check.

        The on-device firmware bundle splits MR536 / InkjetController /
        LaserController / MotionController, but the cloud API treats
        it as one ``content_id``.
        """
        if coordinator.firmware_version:
            return {"main": coordinator.firmware_version}
        return {}
