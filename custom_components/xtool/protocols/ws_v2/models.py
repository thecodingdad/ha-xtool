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
from .protocol_dt001 import DT001WSV2Protocol
from .protocol_m2 import M2WSV2Protocol
from .protocol_p2s import P2SWSV2Protocol


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


# F2 Ultra UV Class 1 — same wire surface as the GS009-CLASS-4 model
# above. The only practical difference is the laser power class
# (Class 1 vs Class 4 per IEC 60825), reflected on the firmware
# side by a separate content_id `xTool-f2-ultra-uv-class1-firmware`
# (added in Studio v1.7.23). Discovery match uses the longer
# "F2UltraUVClass1" suffix so a device that reports it ranks ahead
# of the bare "F2UltraUV" entry; devices that only emit "F2UltraUV"
# (existing Class 4) continue to match the entry above.
XTOOL_F2_ULTRA_UV_CLASS1_WSV2 = XtoolDeviceModel(
    model_id="F2UltraUVClass1",
    name="xTool F2 Ultra UV (Class 1)",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_camera=True,
    camera_names=("main", "deep"),
    has_camera_exposure=True,
    has_fill_light=True,
    has_fill_light_dual=True,
    has_device_sleep=True,
    has_ir_led=True,
    has_button_event=True,
    firmware_content_id="xTool-f2-ultra-uv-class1-firmware",
    firmware_machine_type="MXF",
    protocol_version="V2",
    discovery_match=("F2UltraUVClass1",),
)


XTOOL_M1_WSV2 = XtoolDeviceModel(
    model_id="M1",
    name="xTool M1",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_z_axis=True,
    has_fill_light=True,
    has_mode_switch=True,
    firmware_content_id="xTool-m1-firmware",
    protocol_version="V2",
    discovery_match=("M1",),
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
    has_machine_lock=True,   # M1Ultra bundle exposes workingMode HANDLE/NORMAL
    has_runtime_stats=True,  # M1Ultra bundle exposes standbyTime + printToolType
    has_button_event=True,   # M1Ultra bundle defines LONG_PRESS push handler
    firmware_content_id="xTool-m1-ultra-firmware",
    firmware_machine_type="MLM",
    protocol_version="V2",
    discovery_match=("M1Ultra",),
)


XTOOL_P2_WSV2 = XtoolDeviceModel(
    model_id="P2",
    name="xTool P2",
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
    has_camera=True,
    camera_names=("overview", "closeup"),
    has_camera_exposure=True,
    has_digital_lock=True,
    has_distance_measure=True,
    has_laser_head_position=True,
    has_mode_switch=True,
    has_fill_light=True,
    has_ir_led=True,
    has_gyro=True,
    has_water_cooling=True,
    firmware_content_id="xTool-p2-firmware",
    firmware_machine_type="MXP",
    protocol_version="V2",
    discovery_match=("P2",),
)


XTOOL_P2S_WSV2 = XtoolDeviceModel(
    model_id="P2S",
    name="xTool P2S",
    protocol_class=P2SWSV2Protocol,
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


# Retail Marker (GS008) — added in xTool Studio v1.7.23.
# WS-V2 family, very similar capability set to the F2 family but
# without the dual-channel fill-light and without the depth camera.
# Capability flags conservative — bundle confirms camera (main only),
# fill-light, flame alarm, beep, mode switch, laser_head, smokeFan,
# airassistV2 BT accessory, machine_lock; everything else stays off
# until a real device confirms.
XTOOL_RETAIL_MARKER_WSV2 = XtoolDeviceModel(
    model_id="RetailMarker",
    name="xTool Retail Marker",
    protocol_class=WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_beeper=True,
    has_flame_alarm=True,
    has_move_stop=True,
    has_smoking_fan=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_camera=True,
    camera_names=("main",),
    has_camera_exposure=True,
    has_fill_light=True,
    has_ir_led=True,  # single mention in bundle — leave on; entity is gated to a usable read
    firmware_content_id="xTool-retail-marker-firmware",
    protocol_version="V2",
    discovery_match=("GS008", "RetailMarker"),
)


XTOOL_APPAREL_PRINTER_WSV2 = XtoolDeviceModel(
    model_id="DT001",
    name="xTool Apparel Printer",
    protocol_class=DT001WSV2Protocol,
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


# xTool M2 (model_id JS002) — added in Studio v1.7.23.
#
# Multi-tool device: laser head + inkjet head (verified from
# firmware manifest — 4 controllers: MR536 main MCU plus separate
# InkjetController / LaserController / MotionController). The
# inkjet entity surface is deferred until a real M2 retest
# confirms ``/v1/project/inkjet/*`` response shapes — the bundle
# strings list the routes but not the payload fields. v2.5.14
# ships the core monitor + control surface (status, cover, camera,
# job control) and leaves ``has_inkjet`` as a forward-compat
# capability flag.
XTOOL_M2_WSV2 = XtoolDeviceModel(
    model_id="M2",
    name="xTool M2",
    protocol_class=M2WSV2Protocol,
    coordinator_class=WSV2Coordinator,
    has_camera=True,
    # JS002 bundle exposes ``far`` (global / overview), ``near``
    # (local / close-up), and ``side`` (process-side view). Order
    # is the Studio bundle order; UI lists them in this order too.
    camera_names=("far", "near", "side"),
    has_fill_light=True,
    has_air_assist_state=True,
    has_smoking_fan=True,
    has_cover_sensor=True,
    has_laser_head_position=True,
    has_inkjet=True,
    has_beeper=True,
    has_flame_alarm=True,
    # M2's accessory topology is /v1/platform/accessories/list, not
    # the F-family's /v1/parts/control passthrough. Until the M2
    # accessory listing is wired properly the coordinator's
    # generic M9098 poll would 404 every tick — skip it entirely
    # by opting out of the F-family accessory poll.
    has_bt_accessories=False,
    firmware_content_id="xTool-m2-firmware",
    firmware_machine_type="JS002",
    # Studio's xcs-extension manifest classifies M2 as
    # protocolVersion:"V2", channelType:"socket" — i.e. the
    # standard V2 multi-channel WS framework on port 28900.
    protocol_version="V2",
    discovery_match=("JS002", "M2"),
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
    XTOOL_F2_ULTRA_UV_CLASS1_WSV2,
    XTOOL_RETAIL_MARKER_WSV2,
    # XTOOL_M1_WSV2,  # M1 is V1-only per Studio v1.7.23 manifest
    XTOOL_M1_ULTRA_WSV2,
    # XTOOL_P2_WSV2,  # P2 is V1-only per Studio v1.7.23 manifest — V2 probe never succeeds against real P2 hardware
    XTOOL_P2S_WSV2,
    XTOOL_P3_WSV2,
    XTOOL_METALFAB_WSV2,
    XTOOL_APPAREL_PRINTER_WSV2,
    XTOOL_M2_WSV2,
)
