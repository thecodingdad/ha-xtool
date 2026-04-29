"""Cross-cutting constants for the xTool integration.

Protocol-specific constants (M-codes, HTTP paths, ports unique to one
family, status maps, accessory tables, …) live in the matching
``protocols/<family>.py`` module. Only values used by multiple modules
(or genuinely framework-level — DOMAIN, config keys, status enum, etc.)
belong in this file.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

DOMAIN = "xtool"
DEFAULT_DEVICE_NAME = "xTool Device"

# Config / Options keys
CONF_POWER_SWITCH = "power_switch_entity_id"
CONF_ENABLE_UPDATES = "enable_firmware_updates"
CONF_HAS_AP2 = "has_ap2"  # Opt-in toggle to enable AP2 air cleaner sensors

# --- Network ----------------------------------------------------------------
DEFAULT_SCAN_INTERVAL = 5  # seconds, used by the coordinator

# --- Brightness scaling (S1 fill light + REST fill light entity) ------------
BRIGHTNESS_HA_MAX = 255
BRIGHTNESS_DEVICE_MAX = 100

# --- Cloud firmware update API ---------------------------------------------
FIRMWARE_API_BASE = "https://api.xtool.com/efficacy/v1"
FIRMWARE_CHECK_INTERVAL = 21600  # 6 hours between cloud update checks


# --- Cross-protocol status / sensitivity enums ------------------------------


class XtoolStatus(StrEnum):
    """Normalised status enum used by every protocol's status sensor."""

    OFF = "off"
    INITIALIZING = "initializing"
    IDLE = "idle"
    WIFI_SETUP = "wifi_setup"
    MEASURING = "measuring"
    FRAME_READY = "frame_ready"
    FRAMING = "framing"
    PROCESSING_READY = "processing_ready"
    PROCESSING = "processing"
    PAUSED = "paused"
    FIRMWARE_UPDATE = "firmware_update"
    SLEEPING = "sleeping"
    CANCELLING = "cancelling"
    FINISHED = "finished"
    ERROR_LIMIT = "error_limit"
    ERROR_LASER_CONTROL = "error_laser_control"
    ERROR_LASER_MODULE = "error_laser_module"
    ERROR_FIRE_WARNING = "error_fire_warning"
    ERROR_TILT = "error_tilt"
    ERROR_MOVING = "error_moving"
    WORKING_API = "working_api"
    WORKING_BUTTON = "working_button"
    MEASURE_AREA = "measure_area"
    UNKNOWN = "unknown"


class FlameAlarmSensitivity(IntEnum):
    """Flame alarm sensitivity levels (S1 native scale).

    D-series exposes the same three levels but on a 1/2/3 wire scheme;
    the conversion lives in ``protocols/d_series.py``.
    """

    HIGH = 0
    LOW = 1
    OFF = 2
