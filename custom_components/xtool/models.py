"""Device model definitions for xTool laser cutters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class XtoolDeviceModel:
    """Describes capabilities of a specific xTool device model."""

    model_id: str
    name: str
    protocol_family: str = "ws_mcode"  # ws_mcode, http_mcode, rest
    has_flame_alarm: bool = True
    has_air_assist: bool = True
    has_air_pump_v2: bool = False
    has_smoking_fan: bool = True
    has_xtouch: bool = False
    has_fill_light: bool = True
    has_move_stop: bool = True
    has_beeper: bool = True
    has_z_axis: bool = False
    has_drawer: bool = False
    has_cover_sensor: bool = False
    has_camera: bool = False


# Known device models based on APK analysis
DEVICE_MODELS: dict[str, XtoolDeviceModel] = {
    # S1: WebSocket M-code protocol
    "S1": XtoolDeviceModel(
        model_id="S1",
        name="xTool S1",
        protocol_family="ws_mcode",
        has_air_pump_v2=True,
        has_xtouch=True,
        has_z_axis=True,
        has_cover_sensor=True,
    ),
    # D-series: HTTP M-code protocol
    "D1": XtoolDeviceModel(
        model_id="D1",
        name="xTool D1",
        protocol_family="http_mcode",
        has_smoking_fan=False,
    ),
    "D1Pro": XtoolDeviceModel(
        model_id="D1Pro",
        name="xTool D1 Pro",
        protocol_family="http_mcode",
    ),
    "D1Pro 2.0": XtoolDeviceModel(
        model_id="D1Pro 2.0",
        name="xTool D1 Pro 2.0",
        protocol_family="http_mcode",
    ),
    # M-series: REST API
    "M1": XtoolDeviceModel(
        model_id="M1",
        name="xTool M1",
        protocol_family="rest",
        has_z_axis=True,
    ),
    "M1Ultra": XtoolDeviceModel(
        model_id="M1Ultra",
        name="xTool M1 Ultra",
        protocol_family="rest",
        has_z_axis=True,
        has_drawer=True,
    ),
    # P-series: REST API
    "P2": XtoolDeviceModel(
        model_id="P2",
        name="xTool P2",
        protocol_family="rest",
        has_z_axis=True,
        has_drawer=True,
        has_cover_sensor=True,
        has_camera=True,
    ),
    "P2S": XtoolDeviceModel(
        model_id="P2S",
        name="xTool P2S",
        protocol_family="rest",
        has_z_axis=True,
        has_drawer=True,
        has_cover_sensor=True,
        has_camera=True,
    ),
    "P1": XtoolDeviceModel(
        model_id="P1",
        name="xTool Laserbox",
        protocol_family="rest",
    ),
    # F-series: REST API
    "F1": XtoolDeviceModel(
        model_id="F1",
        name="xTool F1",
        protocol_family="rest",
    ),
    "F1Ultra": XtoolDeviceModel(
        model_id="F1Ultra",
        name="xTool F1 Ultra",
        protocol_family="rest",
    ),
    "GS005": XtoolDeviceModel(
        model_id="GS005",
        name="xTool F1 Lite",
        protocol_family="rest",
    ),
}


def detect_model(device_name: str) -> XtoolDeviceModel:
    """Detect device model from device name string.

    Tries to match known model IDs. Falls back to a generic model.
    """
    name_upper = device_name.upper().replace(" ", "")
    for model_id, model in DEVICE_MODELS.items():
        if model_id.upper().replace(" ", "") in name_upper:
            return model
    # Fallback: generic model with all features enabled
    return XtoolDeviceModel(model_id="unknown", name=device_name, protocol_family="rest")


@dataclass
class XtoolDeviceState:
    """Current state of an xTool device, updated by the coordinator."""

    # Connection
    available: bool = False

    # Status
    status_code: int = -1
    device_name: str = ""
    serial_number: str = ""
    firmware_version: str = ""
    laser: object = None  # LaserInfo, set by coordinator

    # Fill light
    fill_light_a: int = 0
    fill_light_b: int = 0
    light_active: bool = True  # M15: physical on/off (vs M13 configured brightness)

    # Position
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0

    # Settings
    flame_alarm: int = 0  # 0=high, 1=low, 2=off
    fire_level: int = 0
    beeper_enabled: bool = False
    move_stop_enabled: bool = False
    smoking_fan_on: bool = False
    smoking_fan_duration: int = 120
    air_assist_close_delay: int = 10
    air_assist_enabled: bool = False
    air_assist_level: int = 0  # M15 S value: current active gear (0-4)

    # Task
    task_id: str = ""
    task_time: int = 0

    # Lifetime statistics (M2008)
    working_seconds: int | None = None
    session_count: int | None = None
    standby_seconds: int | None = None
    tool_runtime_seconds: int | None = None

    # Diagnostics
    sd_card_present: bool = False
    xtouch_connected: bool = False
    connection_count: int = 0
    accessories_raw: list[str] = field(default_factory=list)
    riser_base: int = 0  # M54 T value: 0=none, 1=present, 2=heightening kit
