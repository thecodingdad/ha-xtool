"""Model registry entries for the WS-V2 family.

Each V1 model that has a V2 firmware sibling gets its own V2 entry here.
``model_id`` matches the V1 sibling so device-name discovery stays
consistent; ``protocol_version="V2"`` disambiguates the two registry
rows. Capability flags mirror the V1 sibling for caps that map 1:1
to the V2 entity set; V1-only caps (`has_ir_led`, `has_distance_measure`,
camera-exposure, etc.) get reset because the V2 endpoint mapping has
not yet been verified.

Future V2-only models (devices that ship without a V1 firmware line)
add a single entry here with ``protocol_version="V2"`` — no V1 stub
sibling needed; the candidate-list step in ``validate_connection``
returns the V2 entry directly.
"""

from __future__ import annotations

from ..base import XtoolDeviceModel

from .coordinator import WSV2Coordinator
from .protocol import WSV2Protocol


XTOOL_F1_WSV2 = XtoolDeviceModel(
    model_id="F1",
    name="xTool F1",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_cover_sensor=True,
    # F1 (basic / portable galvo): no camera, no fill-light, no gantry.
    firmware_content_id="xTool-f1-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F1",),
)


XTOOL_F1_ULTRA_WSV2 = XtoolDeviceModel(
    model_id="F1Ultra",
    name="xTool F1 Ultra",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_uv_fire=True,
    has_gyro=True,
    has_display_screen=True,
    has_camera=True,
    camera_names=("main",),  # Studio F1Ultra V2 bundle → /v1/camera/snap?name=main
    has_camera_exposure=True,
    has_fill_light=True,
    has_fire_record=True,
    has_laser_head_position=True,
    has_cover_sensor=True,
    # No water cooling — fiber + diode galvo, internal/passive cooling.
    firmware_content_id="xTool-f1-ultra-firmware-1.5",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F1Ultra",),
)


# F1 Ultra V2 (GS003) — Class-1 safe variant. The "V2" in the name is
# xTool's product label, not the protocol version. This entry covers
# the V2-firmware sibling of GS003.
XTOOL_F1_ULTRA_V2_WSV2 = XtoolDeviceModel(
    model_id="GS003",
    name="xTool F1 Ultra V2",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_uv_fire=True,
    has_gyro=True,
    has_display_screen=True,
    has_camera=True,
    camera_names=("main",),  # Studio GS003 bundle → /v1/camera/snap?name=main
    has_camera_exposure=True,
    has_fill_light=True,
    has_fire_record=True,
    has_laser_head_position=True,
    has_cover_sensor=True,
    has_ir_led=True,
    # No water cooling — fiber + diode galvo, internal/passive cooling.
    firmware_content_id="xTool-f1-ultra-class1-firmware-1.5",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("GS003",),
)


XTOOL_F1_LITE_WSV2 = XtoolDeviceModel(
    model_id="GS005",
    name="xTool F1 Lite",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_machine_lock=True,
    has_button_event=True,  # GS005 bundle defines /button/status push handler
    firmware_content_id="xTool-f1-lite-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("GS005",),
)


XTOOL_F2_WSV2 = XtoolDeviceModel(
    model_id="F2",
    name="xTool F2",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_camera_exposure=True,
    has_fill_light=True,
    has_fill_light_dual=True,  # GS004 bundle ships front + back channels
    has_device_sleep=True,  # autoSleepEnable in DEVICE_CONFIG push
    firmware_content_id="xTool-f2-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F2",),
)


XTOOL_F2_ULTRA_WSV2 = XtoolDeviceModel(
    model_id="F2Ultra",
    name="xTool F2 Ultra",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_camera=True,
    camera_names=("main", "deep"),  # GS007 cameraMediaManager exposes main + deep
    has_camera_exposure=True,
    has_fill_light=True,
    has_fill_light_dual=True,  # GS007 bundle ships front + back channels
    has_device_sleep=True,  # autoSleepEnable in DEVICE_CONFIG push
    has_button_event=True,  # GS007-CLASS-4 bundle defines /button/status push
    firmware_content_id="xTool-f2-ultra-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F2Ultra",),
)


XTOOL_F2_ULTRA_SINGLE_WSV2 = XtoolDeviceModel(
    model_id="F2UltraSingle",
    name="xTool F2 Ultra (Single)",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_camera=True,
    camera_names=("main", "deep"),  # GS009 cameraMediaManager exposes main + deep
    has_camera_exposure=True,
    has_fill_light=True,
    has_fill_light_dual=True,  # GS009 bundle ships front + back channels
    has_device_sleep=True,  # autoSleepEnable in DEVICE_CONFIG push
    has_button_event=True,  # GS009-CLASS-4 bundle defines /button/status push
    firmware_content_id="xTool-f2-ultra-single-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F2UltraSingle",),
)


XTOOL_F2_ULTRA_UV_WSV2 = XtoolDeviceModel(
    model_id="F2UltraUV",
    name="xTool F2 Ultra UV",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_camera=True,
    camera_names=("main", "deep"),  # GS006 cameraMediaManager exposes main + deep
    has_camera_exposure=True,
    has_fill_light=True,
    has_fill_light_dual=True,  # GS006 bundle ships front + back channels
    has_device_sleep=True,  # autoSleepEnable in DEVICE_CONFIG push (live capture)
    has_z_axis_homing=True,  # GS006 bundle ships ``zAxisReset`` button
    has_ir_led=True,  # F2UV bundle queries `/v1/peripheral/param?type=ir_led`
    has_button_event=True,  # GS006 bundle defines /button/status push handler
    # Air-pump V2 + UV fire sensor are BT-paired accessories on F2UV
    # (`airassistV2` = AirPumpV2, `uv_sensor_wb031` = UvSensor) routed
    # via `/v1/parts/control` — not built-in peripherals; entities
    # would stay Unknown if the flags were set.
    firmware_content_id="xTool-f2-ultra-uv-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F2UltraUV",),
)


XTOOL_M1_ULTRA_WSV2 = XtoolDeviceModel(
    model_id="M1Ultra",
    name="xTool M1 Ultra",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_z_axis=True,
    has_drawer=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_gyro=True,
    has_cpu_fan=True,
    has_fill_light=True,
    has_uv_fire=True,
    has_workhead_id=True,
    has_z_temp=True,
    has_runtime_stats=True,  # M1Ultra bundle exposes standbyTime + printToolType
    has_button_event=True,   # M1Ultra bundle defines LONG_PRESS push handler
    firmware_content_id="xTool-m1-ultra-firmware",
    firmware_machine_type="MLM",
    protocol_version="V2",
    discovery_match=("M1Ultra",),
)


XTOOL_P2S_WSV2 = XtoolDeviceModel(
    model_id="P2S",
    name="xTool P2S",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_z_axis=True,
    has_drawer=True,
    has_cover_sensor=True,
    has_lid_sensor=True,
    has_mode_switch=True,
    has_uv_fire=True,
    has_gyro=True,
    has_camera=True,
    camera_names=("overview", "closeup"),  # P2S retains the V1 dual-camera shape
    has_camera_exposure=True,
    has_digital_lock=True,
    has_distance_measure=True,
    has_fill_light=True,
    has_ir_led=True,
    has_laser_head_position=True,
    has_water_cooling=True,  # 55W CO2 glass tube — water tank + antifreeze
    has_runtime_stats=True,  # P2S bundle exposes standbyTime
    has_button_event=True,   # P2S bundle defines SHORT_PRESS push handler
    firmware_content_id="xTool-p2s-firmware",
    firmware_machine_type="MXP",
    protocol_version="V2",
    discovery_match=("P2S",),
)


XTOOL_P3_WSV2 = XtoolDeviceModel(
    model_id="P3",
    name="xTool P3",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_z_axis=True,
    has_drawer=True,
    has_cover_sensor=True,
    has_lid_sensor=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_water_cooling=True,
    has_camera=True,
    camera_names=("overview", "closeup"),  # P3 retains the V1 dual-camera shape
    has_camera_exposure=True,
    has_digital_lock=True,
    has_distance_measure=True,
    has_fill_light=True,
    has_runtime_stats=True,  # P3 bundle exposes standbyTime
    firmware_content_id="xTool-p3-firmware",
    firmware_machine_type="MXP",
    protocol_version="V2",
    discovery_match=("P3",),
)


XTOOL_METALFAB_WSV2 = XtoolDeviceModel(
    model_id="METALFAB",
    name="xTool MetalFab",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_z_axis=True,
    has_drawer=True,
    has_cover_sensor=True,
    has_lid_sensor=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_gyro=True,
    has_camera=True,
    camera_names=("main", "deep"),  # HJ003 bundle exposes dual main + deep cameras
    has_camera_exposure=True,
    has_laser_head_position=True,
    has_fill_light=True,
    has_water_cooling=True,  # industrial water chiller for 1200W fiber laser
    has_workhead_id=True,    # quick-release welding gun head
    firmware_content_id="xTool-hj003-firmware",
    firmware_machine_type="MHJ",
    protocol_version="V2",
    discovery_match=("METALFAB", "HJ003"),
)


XTOOL_APPAREL_PRINTER_WSV2 = XtoolDeviceModel(
    model_id="DT001",
    name="xTool Apparel Printer",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_move_stop=True,
    has_camera=True,  # 16MP AI camera for nozzle calibration
    has_runtime_stats=True,  # DT001 bundle exposes standbyTime
    # Inkjet DTF device — no laser, no flame alarm, no smoke extraction.
    firmware_content_id="xTool-apparelprinter-firmware-1.5",
    firmware_machine_type="MDT",
    protocol_version="V2",
    discovery_match=("DT001",),
)


WSV2_MODELS: tuple[XtoolDeviceModel, ...] = (
    XTOOL_F1_WSV2,
    XTOOL_F1_ULTRA_WSV2,
    XTOOL_F1_ULTRA_V2_WSV2,
    XTOOL_F1_LITE_WSV2,
    XTOOL_F2_WSV2,
    XTOOL_F2_ULTRA_WSV2,
    XTOOL_F2_ULTRA_SINGLE_WSV2,
    XTOOL_F2_ULTRA_UV_WSV2,
    XTOOL_M1_ULTRA_WSV2,
    XTOOL_P2S_WSV2,
    XTOOL_P3_WSV2,
    XTOOL_METALFAB_WSV2,
    XTOOL_APPAREL_PRINTER_WSV2,
)
