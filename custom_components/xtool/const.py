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

# --- Polling intervals (overridable in the options flow) --------------------
CONF_SCAN_INTERVAL = "scan_interval"  # main poll cadence (seconds)
CONF_FIRMWARE_CHECK_INTERVAL = "firmware_check_interval"  # cloud check (hours)
CONF_AP2_POLL_INTERVAL = "ap2_poll_interval"  # S1 + AP2 (seconds)
CONF_STATS_POLL_INTERVAL = "stats_poll_interval"  # S1 M2008 (seconds)
CONF_DONGLE_POLL_INTERVAL = "dongle_poll_interval"  # S1 M9098 (seconds)

# --- Network ----------------------------------------------------------------
DEFAULT_SCAN_INTERVAL = 5  # seconds, used by the coordinator
DEFAULT_AP2_POLL_INTERVAL = 30  # seconds between explicit M9039 queries
DEFAULT_STATS_POLL_INTERVAL = 300  # seconds between M2008 lifetime polls
DEFAULT_DONGLE_POLL_INTERVAL = 60  # seconds between M9098 BLE polls

# --- Brightness scaling (S1 fill light + REST fill light entity) ------------
BRIGHTNESS_HA_MAX = 255
BRIGHTNESS_DEVICE_MAX = 100

# --- Cloud firmware update API ---------------------------------------------
FIRMWARE_API_BASE = "https://api.xtool.com/efficacy/v1"
FIRMWARE_CHECK_INTERVAL = 21600  # default 6 hours; user-overridable per entry


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
