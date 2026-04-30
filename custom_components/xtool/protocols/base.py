"""Shared base classes, dataclasses and parse helpers for xTool protocols."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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


@dataclass
class XtoolDeviceModel:
    """Capabilities + protocol class for a specific xTool device model."""

    model_id: str
    name: str
    protocol_class: type["XtoolProtocol"] | None = None
    coordinator_class: type["XtoolCoordinator"] | None = None
    has_flame_alarm: bool = True
    has_air_assist_state: bool = False  # REST: poll /peripheral/airassist for connect state
    has_smoking_fan: bool = True
    has_fill_light: bool = True
    has_move_stop: bool = True
    has_beeper: bool = True
    has_z_axis: bool = False
    has_drawer: bool = False
    has_cover_sensor: bool = False
    has_camera: bool = False
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
    has_fill_light_rest: bool = False
    has_water_cooling: bool = False  # REST: F1 Ultra fiber-laser water loop
    has_z_temp: bool = False  # REST: M1 Ultra Z-axis NTC temperature
    has_workhead_id: bool = False  # REST: M1 Ultra detects mounted tool head
    has_cpu_fan: bool = False  # REST: M1 Ultra CPU fan
    has_uv_fire: bool = False  # REST: F1U / M1U / P2S UV-based fire sensor
    has_gyro: bool = False  # REST: P2 / P2S / F1U / M1U accelerometer
    has_display_screen: bool = False  # REST: F1 Ultra touchscreen brightness
    firmware_content_id: str = ""
    firmware_multi_package: bool = False
    firmware_board_ids: tuple[str, ...] = ()
    firmware_machine_type: str = ""
    firmware_flash_strategy: str = "default"  # REST family: "default" or "m1_four_step"


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

    # REST diagnostic / config — last_button_event reused from F1V2
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
    machine_lock_check_enabled: bool = False
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
