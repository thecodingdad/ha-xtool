"""Constants for the xTool integration."""

from __future__ import annotations

from enum import IntEnum, StrEnum

DOMAIN = "xtool"
CONF_POWER_SWITCH = "power_switch_entity_id"
CONF_ENABLE_UPDATES = "enable_firmware_updates"
DEFAULT_DEVICE_NAME = "xTool Device"

# --- Network ---
DEFAULT_WS_PORT = 8081
DEFAULT_HTTP_PORT = 8080
DEFAULT_SCAN_INTERVAL = 5  # seconds

# --- Brightness scaling ---
BRIGHTNESS_HA_MAX = 255
BRIGHTNESS_DEVICE_MAX = 100

# --- Time ---
SECONDS_PER_HOUR = 3600

# --- M-Code Commands (sent via WebSocket or HTTP) ---
# Common query commands (send as-is, device echoes back with parameters)
CMD_DEVICE_STATUS = "M222"
CMD_IP_ADDRESS = "M2002"
CMD_FULL_INFO = "M2003"
CMD_FILL_LIGHT = "M13"
CMD_FLAME_ALARM = "M340"
CMD_FIRE_LEVEL = "M343"
CMD_SMOKING_FAN = "M7"
CMD_AIR_ASSIST = "M15"
CMD_MOVE_STOP = "M318"
CMD_BEEPER = "M21"
CMD_AIR_ASSIST_DELAY = "M1099"
CMD_LASER_COORD = "M303"
CMD_TASK_ID = "M810"
CMD_TASK_TIME = "M815"
CMD_DEVICE_NAME = "M100"
CMD_POSITION = "M27"
CMD_XTOUCH_STATUS = "M362"
CMD_SD_CARD = "M321"
CMD_LIGHT_INTERFERENCE = "M2240"
CMD_Z_OFFSET = "M1113"
CMD_AIRFLOW_V2 = "M9009"
CMD_ACCESSORIES = "M1098"
CMD_LIFETIME_STATS = "M2008"
CMD_LIGHT_ACTIVE = "M15"  # Also returns air assist status: A{enabled} S{gear}
CMD_PROBE_Z = "M313"
CMD_SERIAL_NUMBER = "M310"
CMD_FIRMWARE_VERSION = "M99"
CMD_LASER_INFO = "M116"
CMD_RISER_BASE = "M54"

# D-series specific commands
CMD_STATUS_D_SERIES = "M96"
CMD_POSITION_D_SERIES = "M304"
CMD_FLAME_ALARM_D_SERIES = "M310"
CMD_FLAME_SENSITIVITY_D_SERIES = "M309"

# Control commands
CMD_PAUSE_JOB = "M22 S1"
CMD_RESUME_JOB = "M22 S0"
CMD_CANCEL_JOB = "M108"
CMD_HOME_ALL = "M111 S7"
CMD_HOME_XY = "M111 S3"
CMD_HOME_Z = "M111 S2"

# --- XCS Compatibility Mode ---
XCS_KICK_LIMIT = 3  # disconnects within window to trigger XCS mode
XCS_KICK_WINDOW = 30.0  # seconds
XCS_RECOVERY_INTERVAL = 60.0  # seconds between WS reconnect attempts in XCS mode
XCS_KICK_DETECTION_SECONDS = 10.0  # session shorter than this = "kicked"
STATS_POLL_INTERVAL = 300  # seconds between M2008 polls (5 minutes)

# --- Firmware Update API ---
FIRMWARE_API_BASE = "https://api.xtool.com/efficacy/v1"
FIRMWARE_CHECK_INTERVAL = 21600  # 6 hours between update checks

# --- M2003 JSON Response Keys ---
# Keys inside the JSON object returned by CMD_FULL_INFO (M2003)
INFO_KEY_SERIAL_NUMBER = "M310"
INFO_KEY_DEVICE_NAME = "M100"
INFO_KEY_POWER_INFO = "M116"  # e.g. "X0Y20B1P1L3" where Y=watts
INFO_KEY_MAIN_FIRMWARE = "M99"
INFO_KEY_LASER_FIRMWARE = "M1199"
INFO_KEY_WIFI_FIRMWARE = "M2099"
INFO_KEY_ACCESSORIES = "M1098"
INFO_KEY_FILL_LIGHT = "M13"
INFO_KEY_STATUS = "M222"
INFO_KEY_POSITION = "M27"


class XtoolStatus(StrEnum):
    """Device status codes from M222 response."""

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
    MEASURE_AREA = "measure_area"
    UNKNOWN = "unknown"


# M222 S-code to XtoolStatus mapping
M222_STATUS_MAP: dict[int, XtoolStatus] = {
    0: XtoolStatus.INITIALIZING,
    1: XtoolStatus.IDLE,
    2: XtoolStatus.WIFI_SETUP,
    3: XtoolStatus.IDLE,
    4: XtoolStatus.ERROR_LIMIT,
    7: XtoolStatus.ERROR_LASER_MODULE,
    9: XtoolStatus.ERROR_LIMIT,  # fire alarm triggered
    10: XtoolStatus.MEASURING,
    11: XtoolStatus.FRAME_READY,
    12: XtoolStatus.FRAMING,
    13: XtoolStatus.PROCESSING_READY,
    14: XtoolStatus.PROCESSING,
    15: XtoolStatus.PAUSED,
    16: XtoolStatus.FIRMWARE_UPDATE,
    17: XtoolStatus.SLEEPING,
    18: XtoolStatus.CANCELLING,
    19: XtoolStatus.FINISHED,
    20: XtoolStatus.ERROR_LIMIT,
    21: XtoolStatus.ERROR_LASER_CONTROL,
    22: XtoolStatus.ERROR_LASER_MODULE,
    24: XtoolStatus.MEASURE_AREA,
}

# Status codes used in D-series HTTP M-code protocol
STATUS_CODE_IDLE = 3
STATUS_CODE_PROCESSING = 14


class FlameAlarmSensitivity(IntEnum):
    """Flame alarm sensitivity levels."""

    HIGH = 0
    LOW = 1
    OFF = 2


# --- M1098 Accessories Array Positions ---
ACCESSORY_IDX_PURIFIER = 0
ACCESSORY_IDX_FIRE_EXTINGUISHER = 1
ACCESSORY_IDX_AIR_PUMP_V1 = 2
ACCESSORY_IDX_AIR_PUMP_V2 = 3
ACCESSORY_IDX_FIRE_EXTINGUISHER_V15 = 4

ACCESSORY_NAMES: dict[int, str] = {
    ACCESSORY_IDX_PURIFIER: "Purifier",
    ACCESSORY_IDX_FIRE_EXTINGUISHER: "Fire Extinguisher",
    ACCESSORY_IDX_AIR_PUMP_V1: "Air Pump 1.0",
    ACCESSORY_IDX_AIR_PUMP_V2: "Air Pump 2.0",
    ACCESSORY_IDX_FIRE_EXTINGUISHER_V15: "Fire Extinguisher v1.5",
}

RISER_BASE_NAMES: dict[int, str] = {
    1: "Riser base",
    2: "Heightening kit",
}

# --- Laser Module Info (parsed from INFO_KEY_POWER_INFO / M116) ---
LASER_TYPE_NAMES: dict[int, str] = {
    0: "Diode",
    1: "Infrared",
}

LASER_POWERS_IR = {2, 3}


def get_laser_type_name(laser_type: int, power_watts: int) -> str:
    """Get human-readable laser type name."""
    if power_watts in LASER_POWERS_IR:
        return "Infrared"
    if power_watts > 0:
        return "Diode"
    return LASER_TYPE_NAMES.get(laser_type, "Unknown")
