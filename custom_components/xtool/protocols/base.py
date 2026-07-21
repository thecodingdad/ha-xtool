"""Shared base classes, dataclasses and parse helpers for xTool protocols."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..const import DEFAULT_DEVICE_NAME

if TYPE_CHECKING:
    from typing import Any  # noqa: F401
    from ..const import XtoolStatus
    from ..coordinator import XtoolCoordinator

_LOGGER = logging.getLogger(__name__)

# Regex used by every M-code-speaking protocol to extract parameters from a
# response, e.g. "M13 A70 B70" -> {"A": "70", "B": "70"}.
_PARAM_RE = re.compile(r"([A-Z])(-?[\d.]+)")


# --- Dataclasses describing devices and their state -------------------------


@dataclass
class LaserInfo:
    """Parsed laser module info (S1 M116 or REST /getlaserpowerinfo)."""

    laser_type: int = 0
    power_watts: int = 0
    laser_producer: int = 0
    process_type: int = 0
    laser_tube: int = 0

    @property
    def type_name(self) -> str:
        # Lazy import — laser type names live in the S1 module.
        from .s1 import get_laser_type_name

        return get_laser_type_name(self.laser_type, self.power_watts)

    @property
    def description(self) -> str:
        if self.power_watts == 0:
            return "Not detected"
        return f"{self.power_watts}W {self.type_name}"


@dataclass
class DeviceInfo:
    """Static device information shared across protocols."""

    serial_number: str = ""
    device_name: str = ""
    laser: LaserInfo = field(default_factory=LaserInfo)
    main_firmware: str = ""
    laser_firmware: str = ""
    wifi_firmware: str = ""
    mac_address: str = ""
    workspace_x: float = 0.0
    workspace_y: float = 0.0
    workspace_z: float = 0.0

    @property
    def laser_power_watts(self) -> int:
        return self.laser.power_watts


@dataclass
class ConnectionInfo:
    """Result of a successful connection validation."""

    host: str = ""
    name: str = ""
    serial_number: str = ""
    firmware_version: str = ""
    laser_power_watts: int = 0
    device_info: DeviceInfo | None = None
    # "V1" for legacy HTTP REST / WS-mcode / D-series, "V2" when the
    # device's TLS WebSocket on port 28900 answered the probe. Persisted
    # in the config entry so future setups skip the probe.
    protocol_version: str = "V1"
    # Resolved model id (matches ``XtoolDeviceModel.model_id`` of the
    # candidate that won the V1/V2 selection). Persisted to entry data so
    # ``async_setup_entry`` re-resolves the exact entry on every reload.
    model_id: str = ""


@dataclass
class AccessoryState:
    """Live state of one BT-paired accessory.

    ``type_id`` matches an ``AccessoryDefinition.type_id`` from
    :mod:`accessories.definitions`; ``sn`` is the per-device serial.
    ``fields`` carries the parsed M-code response (see
    :mod:`accessories.mcodes`) and entity ``value_fn`` lookups read
    from there. ``last_seen`` lets the coordinator decide when a
    formerly-paired accessory has dropped off the dongle without
    being explicitly unbound.
    """

    type_id: str
    sn: str
    fields: dict[str, Any] = field(default_factory=dict)
    last_seen: float = 0.0


@dataclass
class XtoolDeviceModel:
    """Capabilities + protocol class for a specific xTool device model."""

    model_id: str
    name: str
    protocol_class: type["XtoolProtocol"] | None = None
    coordinator_class: type["XtoolCoordinator"] | None = None
    has_flame_alarm: bool = False
    has_air_assist_state: bool = False  # REST: poll /peripheral/airassist for connect state
    has_smoking_fan: bool = False
    has_fill_light: bool = False
    has_fill_light_dual: bool = False  # F-family V2: separate Front + Back channels
    has_device_sleep: bool = False  # ``autoSleepEnable`` config bool
    has_move_stop: bool = False
    has_beeper: bool = False
    has_z_axis: bool = False
    has_drawer: bool = False
    has_cover_sensor: bool = False
    has_camera: bool = False
    # Per-camera labels the V2 firmware accepts as the ``name``
    # query parameter of ``GET /v1/camera/snap``. Each entry yields
    # one camera entity (snap + live MJPEG). When ``has_camera`` is
    # set but this tuple is empty, the build skips camera entities.
    # - GS003 / F1Ultra              → ``("main",)``
    # - GS004 / GS006 / GS007 /
    #   GS009 / HJ003 (F2 family +
    #   MetalFab)                    → ``("main", "deep")``
    # - P2 / P2S / P3                → ``("overview", "closeup")``
    #   (V1 legacy naming carried through to their V2 variant).
    camera_names: tuple[str, ...] = ()
    has_tilt_sensor: bool = False
    has_moving_sensor: bool = False
    has_limit_switch: bool = False
    has_lid_sensor: bool = False
    has_machine_lock: bool = False
    has_ir_led: bool = False
    has_digital_lock: bool = False
    has_distance_measure: bool = False
    has_camera_exposure: bool = False
    has_fire_record: bool = False
    has_laser_head_position: bool = False
    has_mode_switch: bool = False
    has_purifier_timeout: bool = False
    has_water_cooling: bool = False  # REST: F1 Ultra fiber-laser water loop
    has_z_temp: bool = False  # REST: M1 Ultra Z-axis NTC temperature
    has_workhead_id: bool = False  # REST: M1 Ultra detects mounted tool head
    has_cpu_fan: bool = False  # REST: M1 Ultra CPU fan
    has_uv_fire: bool = False  # REST: F1U / M1U / P2S UV-based fire sensor
    has_gyro: bool = False  # REST: P2 / P2S / F1U / M1U accelerometer
    has_display_screen: bool = False  # REST: F1 Ultra touchscreen brightness
    has_cooling_fan: bool = False  # WS-V2: built-in cooling-fan peripheral
    has_runtime_stats: bool = False  # WS-V2: /v1/device/statistics exposes
    # last_job_time / working_seconds / standby_seconds / tool_runtime / print_tool_type
    has_button_event: bool = False  # WS-V2: /button/status push fires
    has_inkjet: bool = False  # M2: inkjet head + /v1/project/inkjet/*
    # BT accessory subsystem. Default True — every Studio bundle (S1
    # / D-series / REST / WS-V2) defines ``getAllDangleConnectList``
    # (M9098), and the firmware returns an empty list when no dongle
    # is attached, so the per-poll cost on accessory-less devices is
    # one no-op M-code per cycle. Models that demonstrably can't
    # tunnel BT (e.g. firmware bundles that confirm no
    # ``/passthrough`` or ``/v1/parts/control`` route) opt out
    # explicitly with ``has_bt_accessories=False``.
    has_bt_accessories: bool = True
    firmware_content_id: str = ""
    firmware_multi_package: bool = False
    firmware_board_ids: tuple[str, ...] = ()
    firmware_machine_type: str = ""
    firmware_flash_strategy: str = "default"  # REST family: "default" or "m1_four_step"
    # Protocol version this entry targets. ``"V1"`` for legacy HTTP REST /
    # WS-mcode / D-series, ``"V2"`` for the WS-tunneled API on port 28900.
    # Two registry entries can share the same ``model_id`` as long as they
    # differ in ``protocol_version`` — they represent the same physical
    # device on different firmware lines.
    protocol_version: str = "V1"
    # Substrings to match against the discovered device name. Empty defaults
    # to ``(model_id,)``. Both V1 and V2 siblings of the same device share
    # the same discovery_match so the candidate-list step in
    # ``validate_connection`` returns both.
    discovery_match: tuple[str, ...] = ()


@dataclass
class XtoolDeviceState:
    """Current state of an xTool device, updated by the coordinator."""

    # Connection
    available: bool = False

    # Status — each protocol's poll_state writes the canonical XtoolStatus
    # enum directly; raw → enum mapping lives in the family protocol module.
    status: "XtoolStatus | None" = None
    device_name: str = ""
    serial_number: str = ""
    firmware_version: str = ""
    laser: object = None  # LaserInfo, set by coordinator

    # Fill light
    fill_light_a: int = 0
    fill_light_b: int = 0
    light_active: bool = True

    # Position
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0

    # Settings
    # ``flame_alarm`` carries the legacy 3-state sensitivity int
    # used by S1 (M340) / REST V1 / D-series — those firmwares
    # genuinely expose sensitivity levels. V2 firmware (F-family,
    # P-family, MetalFab) types ``flameAlarm`` strictly as boolean
    # and is served by the ``flame_alarm_v2`` switch instead.
    flame_alarm: int = 0
    fire_level: int = 0
    beeper_enabled: bool = False
    move_stop_enabled: bool = False
    smoking_fan_on: bool = False
    smoking_fan_duration: int = 120
    air_assist_close_delay: int = 10
    air_assist_enabled: bool = False
    air_assist_level: int = 0
    air_assist_connected: bool | None = None  # REST: /peripheral/airassist state
    air_assist_gear_cut: int = 0  # REST: airassistCut config (default cut gear)
    air_assist_gear_grave: int = 0  # REST: airassistGrave config (default engrave gear)

    # REST diagnostic / config — last_button_event reused from WS-V2
    sleep_timeout: int | None = None  # /getsleeptimeout (seconds)
    sleep_timeout_open_gap: int | None = None  # /getsleeptimeoutopengap
    fill_light_auto_off: int | None = None  # /getFilllightAutoClosetimout
    ir_light_auto_off: int | None = None  # /getIrlightAutoClosetimout
    beep_enabled: bool | None = None  # /getBeepEnable
    drawer_check: bool | None = None  # /getdrawercheck
    filter_check: bool | None = None  # /getfiltercheck
    purifier_check: bool | None = None  # /getpurifiercheck
    purifier_continue: bool | None = None  # /getpurifiercontinue
    print_tool_type: str = ""  # /getprintToolType
    hardware_type: str = ""  # /gethardwaretype
    water_temperature: float | None = None  # F1 Ultra
    water_flow: float | None = None  # F1 Ultra
    z_temperature: float | None = None  # M1 Ultra
    workhead_id: str = ""  # M1 Ultra mounted tool head
    workhead_z_height: float | None = None  # M1 Ultra
    flame_level_hl: int | None = None  # config kv flameLevelHLSelect (1=high, 2=low)
    # Push peripheral states
    drawer_open: bool | None = None  # /peripheral/drawer
    cooling_fan_running: bool | None = None  # /peripheral/cooling_fan
    smoking_fan_running: bool | None = None  # /peripheral/smoking_fan (REST)
    cpu_fan_running: bool | None = None  # /peripheral/cpu_fan (M1U)
    uv_fire_alarm: bool | None = None  # /peripheral/uv_fire_sensor
    water_pump_running: bool | None = None  # /peripheral/water_pump (F1U)
    water_line_ok: bool | None = None  # /peripheral/water_line (F1U)
    gyro_x: float | None = None
    gyro_y: float | None = None
    gyro_z: float | None = None
    # D-series
    redcross_mode: int | None = None  # M97 S0=cross, S1=lowlight
    work_area_left: int = 0
    work_area_right: int = 0
    work_area_up: int = 0
    work_area_down: int = 0
    display_brightness: int | None = None  # F1 Ultra digital_screen
    # Bluetooth dongle accessories (S1)
    ble_accessories: list[dict] | None = None

    # Task
    task_id: str = ""
    task_time: int = 0
    # 0.0–100.0 percent. Only populated by families whose progress
    # endpoint actually returns a percent value (currently D-series
    # ``/progress.progress``); other families leave it at ``None``
    # so the sensor stays unavailable.
    task_progress: float | None = None

    # Lifetime statistics (S1 M2008)
    working_seconds: int | None = None
    session_count: int | None = None
    standby_seconds: int | None = None
    tool_runtime_seconds: int | None = None

    # Diagnostics
    sd_card_present: bool = False
    connection_count: int = 0
    accessories_raw: list[str] = field(default_factory=list)
    riser_base: int = 0

    # BT accessory subsystem — populated by ``coordinator._poll_accessories``
    # for every model with ``has_bt_accessories=True``. Keyed by
    # ``"<type_id>:<sn>"`` (sn falls back to the dongle slot index when the
    # accessory doesn't report one). Each entry carries the decoded
    # M-code fields per :func:`accessories.mcodes.parse_*` plus the
    # accessory-type metadata from :class:`AccessoryDefinition`.
    connected_accessories: dict[str, "AccessoryState"] = field(default_factory=dict)

    # D-series safety
    tilt_stop_enabled: bool = False
    moving_stop_enabled: bool = False
    limit_stop_enabled: bool = False
    tilt_threshold: int = 0
    moving_threshold: int = 0
    flame_alarm_mode: int = 0
    origin_offset_x: float = 0.0
    origin_offset_y: float = 0.0

    # Cover/lid (F1 V2 push events + REST /peripheral/gap)
    cover_open: bool | None = None
    cover_locked: bool | None = None

    # M2 inkjet head — populated by ``M2WSV2Protocol.poll_state`` on
    # models with ``has_inkjet=True``. Read-only surface pulled from
    # ``/v1/project/inkjet/{ink-volume,cap-status,ink-status,info}``.
    # Ink-volume value range is raw numeric until a live device
    # confirms the unit (0-100 percent likely, but bundle lacks an
    # explicit ceiling).
    inkjet_ink_c: int | None = None
    inkjet_ink_m: int | None = None
    inkjet_ink_y: int | None = None
    inkjet_ink_k: int | None = None
    inkjet_head_capped: bool | None = None
    inkjet_toner_installed: bool | None = None
    inkjet_sn: str = ""
    inkjet_version: str = ""
    inkjet_toner_sn: str = ""
    inkjet_calibrated: bool | None = None
    machine_lock: bool | None = None

    # REST IR LEDs
    ir_led_close: bool = False
    ir_led_global: bool = False

    # REST camera exposure
    camera_exposure_overview: int = 0
    camera_exposure_closeup: int = 0

    # REST distance measurement
    last_distance_mm: float | None = None

    # AP2 air cleaner
    purifier_speed: int = 0
    purifier_on: bool = False
    purifier_sensor_d: int | None = None
    purifier_sensor_s: int | None = None
    filter_pre: int | None = None
    filter_medium: int | None = None
    filter_carbon: int | None = None
    filter_dense_carbon: int | None = None
    filter_hepa: int | None = None
    purifier_alarm: bool = False
    alarm_present: bool = False

    # F1 V2 push config + diagnostics
    flame_alarm_v2_enabled: bool = False
    beep_enabled_v2: bool = False
    gap_check_enabled: bool = False
    machine_lock_check_enabled: bool = False  # legacy — kept for state-restore compatibility
    stops_when_moved: bool = False  # mirrors workingMode enum: HANDLE=True, NORMAL=False
    auto_sleep_enable: bool = True  # mirrors autoSleepEnable config bool
    purifier_timeout: int = 0
    working_mode: str = ""
    last_button_event: str = ""
    last_job_time_seconds: int = 0


# --- Generic M-code parse helpers (used by S1 + D-series) --------------------


def parse_params(response: str) -> dict[str, str]:
    """Parse parameter values from a response: "M13 A70 B70" -> {A:70, B:70}."""
    return dict(_PARAM_RE.findall(response))


def parse_param_int(response: str, key: str, default: int = 0) -> int:
    params = parse_params(response)
    try:
        return int(params.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def parse_param_float(response: str, key: str, default: float = 0.0) -> float:
    params = parse_params(response)
    try:
        return float(params.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def parse_quoted_string(response: str) -> str | None:
    """Extract the first quoted substring: 'M100 "xTool S1"' -> 'xTool S1'."""
    match = re.search(r'"([^"]*)"', response)
    return match.group(1) if match else None


# --- Firmware update contract ----------------------------------------------


@dataclass
class FirmwareFile:
    """A single firmware file to download and flash."""

    board_id: str
    name: str
    url: str
    md5: str
    file_size: int
    burn_type: str = ""


@dataclass
class FirmwareUpdateInfo:
    """Information about an available firmware update."""

    latest_version: str
    release_summary: str
    files: list[FirmwareFile] = field(default_factory=list)
    total_size: int = 0
    board_versions: dict[str, str] = field(default_factory=dict)


# --- Abstract protocol base --------------------------------------------------


class XtoolProtocol(ABC):
    """Abstract base class for xTool device communication protocols."""

    def __init__(self, host: str) -> None:
        self.host = host

    @property
    @abstractmethod
    def connected(self) -> bool:
        ...

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @abstractmethod
    async def get_version(self) -> str | None:
        ...

    @abstractmethod
    async def get_device_info(self) -> DeviceInfo:
        ...

    async def validate(self) -> ConnectionInfo | None:
        try:
            await self.connect()
            info = await self.get_device_info()
            version = await self.get_version()
            firmware = info.main_firmware or version or ""
            return ConnectionInfo(
                host=self.host,
                name=info.device_name or DEFAULT_DEVICE_NAME,
                serial_number=info.serial_number,
                firmware_version=firmware,
                laser_power_watts=info.laser_power_watts,
                device_info=info,
            )
        except Exception as err:
            _LOGGER.debug("Validation failed for %s: %s", self.host, err)
            return None
        finally:
            await self.disconnect()

    # --- Firmware update API --------------------------------------------
    # Default implementations cover protocols without firmware-update
    # support (F1 V2 listener, generic fallback). Each protocol that
    # supports flashing overrides flash_firmware().

    async def get_firmware_versions(self, coordinator) -> dict[str, str]:
        """Per-board current firmware versions for the cloud-update check.

        Multi-package protocols (S1) return a board_id → version dict;
        single-package protocols return ``{"main": <version>}``. ``coordinator``
        provides access to cached values populated during get_device_info.
        """
        return {"main": coordinator.firmware_version} if coordinator.firmware_version else {}

    async def flash_firmware(
        self,
        files: list["FirmwareFile"],
        blobs: list[bytes],
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """Flash one or more firmware files to the device.

        Each protocol decides how to consume the list:

        - S1 iterates the boards sequentially (each entry → one ``/burn`` POST).
        - D-series receives a single-element list and POSTs the blob to ``/upgrade``.
        - REST family default treats it as single-blob; the M1 four-step
          strategy expects two entries (script + package) in that order.

        Default raises NotImplementedError so devices that do not implement
        flashing fail fast when the user attempts to install.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement firmware flashing"
        )

    async def upload_accessory_firmware(
        self,
        accessory_type_id: str,
        blob: bytes,
        md5: str,
        filename: str,
        progress_cb: Callable[[float], None] | None = None,
    ) -> None:
        """Upload + trigger a firmware flash on a BT / wired accessory.

        Studio's three-stage flow (mirrored verbatim for S1 / REST V1 /
        D-series — these families serve the same `/parts` + `/v1/parts/firmware`
        endpoints on the laser's main HTTP port 8080):

        1. ``POST /parts`` (multipart, ``filetype=4&filename=<md5>.<ext>&md5=<md5>``)
        2. wait ~5 s, then ``POST /v1/parts/firmware/upgrade`` with
           ``{filename:"<md5>.<ext>"}``
        3. poll ``GET /v1/parts/firmware/upgrade-progress`` until done

        WS-V2 firmware uses a different shape: the blob rides the
        ``file_stream`` channel with ``fileType:2`` and the trigger goes
        through ``parts_control`` against ``/v1/platform/accessories/upgrade``.

        Default raises NotImplementedError so the update entity can
        surface a useful error if a family is missing the implementation.

        ``accessory_type_id`` is the symbolic id ("DuctFan", "Purifier",
        …); each protocol maps it to the numeric Te value when needed.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement accessory firmware upload"
        )
