"""Model registry entries for the REST family."""

from __future__ import annotations

from ..base import XtoolDeviceModel
from .coordinator import RestCoordinator
from .protocol import RestProtocol

XTOOL_M1 = XtoolDeviceModel(
    model_id="M1",
    name="xTool M1",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_z_axis=True,
    has_fill_light_rest=True,
    has_mode_switch=True,
    firmware_content_id="xTool-m1-firmware",
    firmware_flash_strategy="m1_four_step",
)

XTOOL_M1_ULTRA = XtoolDeviceModel(
    model_id="M1Ultra",
    name="xTool M1 Ultra",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_z_axis=True,
    has_drawer=True,
    has_fill_light_rest=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_z_temp=True,
    has_workhead_id=True,
    has_cpu_fan=True,
    has_uv_fire=True,
    has_gyro=True,
    firmware_content_id="xTool-m1-ultra-firmware",
    firmware_machine_type="MLM",
)

XTOOL_P2 = XtoolDeviceModel(
    model_id="P2",
    name="xTool P2",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_z_axis=True,
    has_drawer=True,
    has_cover_sensor=True,
    has_camera=True,
    has_lid_sensor=True,
    has_ir_led=True,
    has_digital_lock=True,
    has_distance_measure=True,
    has_camera_exposure=True,
    has_laser_head_position=True,
    has_mode_switch=True,
    has_fill_light_rest=True,
    has_gyro=True,
    firmware_content_id="xTool-p2-firmware",
    firmware_machine_type="MXP",
)

XTOOL_P2S = XtoolDeviceModel(
    model_id="P2S",
    name="xTool P2S",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_z_axis=True,
    has_drawer=True,
    has_cover_sensor=True,
    has_camera=True,
    has_lid_sensor=True,
    has_ir_led=True,
    has_digital_lock=True,
    has_distance_measure=True,
    has_camera_exposure=True,
    has_laser_head_position=True,
    has_mode_switch=True,
    has_fill_light_rest=True,
    has_uv_fire=True,
    has_gyro=True,
    firmware_content_id="xTool-p2s-firmware",
    firmware_machine_type="MXP",
)

XTOOL_P1 = XtoolDeviceModel(
    model_id="P1",
    name="xTool Laserbox",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    firmware_content_id="xTool-p1-firmware",
)

XTOOL_F1 = XtoolDeviceModel(
    model_id="F1",
    name="xTool F1",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_camera_exposure=True,
    has_laser_head_position=True,
    has_fill_light_rest=True,
    has_machine_lock=True,
    has_mode_switch=True,
    firmware_content_id="xTool-f1-firmware",
)

XTOOL_F1_ULTRA = XtoolDeviceModel(
    model_id="F1Ultra",
    name="xTool F1 Ultra",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_camera=True,
    has_camera_exposure=True,
    has_fire_record=True,
    has_laser_head_position=True,
    has_fill_light_rest=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_water_cooling=True,
    has_uv_fire=True,
    has_gyro=True,
    has_display_screen=True,
    firmware_content_id="xTool-f1-ultra-firmware-1.5",
    firmware_machine_type="MXF",
)

XTOOL_GS005 = XtoolDeviceModel(
    model_id="GS005",
    name="xTool F1 Lite",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_machine_lock=True,
    firmware_content_id="xTool-f1-lite-firmware",
    firmware_machine_type="MXF",
)

# xTool MetalFab — fiber-laser metal welding/cutting station (extId HJ003,
# 1200 W fiber laser). REST family. Reports `weld_machine` accessory + GD470
# motion + RK3568 main MCU. Capabilities and protocol metadata extracted
# from the xTool Studio Windows app's `exts.zip/HJ003/index.js` and a real
# device dump in https://github.com/BassXT/xtool/issues/18:
#
#   - firmware_content_id: "xTool-hj003-firmware"  (newer xTool Studio prefix)
#   - firmware machine_type: "MHJ"  (used in the /upgrade_version handshake)
#   - REST endpoints exposed: /peripheral/{laser_head, fill_light, gap,
#     drawer, airassistV, param}, /device/{machineInfo, runningStatus,
#     workingInfo, accessory/control}, /processing/{upload, progress, …}.
XTOOL_P3 = XtoolDeviceModel(
    model_id="P3",
    name="xTool P3",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_z_axis=True,
    has_drawer=True,
    has_cover_sensor=True,
    has_lid_sensor=True,
    has_camera=True,
    has_camera_exposure=True,
    has_distance_measure=True,
    has_digital_lock=True,
    has_fill_light_rest=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_water_cooling=True,  # peripheral/water_temp+water_flow seen
    firmware_content_id="xTool-p3-firmware",
    firmware_machine_type="MXP",
)

# F2 (GS006) — successor to F1, basic 60 W diode/fiber laser station.
XTOOL_F2 = XtoolDeviceModel(
    model_id="F2",
    name="xTool F2",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_camera_exposure=True,
    has_laser_head_position=True,
    has_fill_light_rest=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    firmware_content_id="xTool-f2-firmware",
    firmware_machine_type="MXF",
)

# F2 Ultra (GS004-CLASS-4) — Class-4 enclosed station with safety + emergency stop.
XTOOL_F2_ULTRA = XtoolDeviceModel(
    model_id="F2Ultra",
    name="xTool F2 Ultra",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_camera=True,
    has_camera_exposure=True,
    has_laser_head_position=True,
    has_fill_light_rest=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    firmware_content_id="xTool-f2-ultra-firmware",
    firmware_machine_type="MXF",
)

# F2 Ultra Single (GS007-CLASS-4) — single-laser variant.
XTOOL_F2_ULTRA_SINGLE = XtoolDeviceModel(
    model_id="F2UltraSingle",
    name="xTool F2 Ultra (Single)",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_camera=True,
    has_camera_exposure=True,
    has_laser_head_position=True,
    has_fill_light_rest=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    firmware_content_id="xTool-f2-ultra-single-firmware",
    firmware_machine_type="MXF",
)

# F2 Ultra UV (GS009-CLASS-4) — UV laser variant.
XTOOL_F2_ULTRA_UV = XtoolDeviceModel(
    model_id="F2UltraUV",
    name="xTool F2 Ultra UV",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_camera=True,
    has_camera_exposure=True,
    has_laser_head_position=True,
    has_fill_light_rest=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_uv_fire=True,
    firmware_content_id="xTool-f2-ultra-uv-firmware",
    firmware_machine_type="MXF",
)

# F1 Ultra V2 (GS003) — Class-1 safe variant of F1 Ultra (newer firmware bundle).
XTOOL_F1_ULTRA_V2 = XtoolDeviceModel(
    model_id="GS003",
    name="xTool F1 Ultra V2",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_camera=True,
    has_camera_exposure=True,
    has_fire_record=True,
    has_laser_head_position=True,
    has_fill_light_rest=True,
    has_machine_lock=True,
    has_mode_switch=True,
    has_water_cooling=True,
    has_uv_fire=True,
    has_gyro=True,
    has_display_screen=True,
    firmware_content_id="xTool-f1-ultra-class1-firmware-1.5",
    firmware_machine_type="MXF",
)

# Apparel Printer (DT001) — DTG/DTF printer with ink/water/heater peripherals.
# Doesn't fit the laser-centric capability flags well; kept for discovery
# completeness so the device shows up with status/firmware sensors.
XTOOL_APPAREL_PRINTER = XtoolDeviceModel(
    model_id="DT001",
    name="xTool Apparel Printer",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    firmware_content_id="xTool-apparelprinter-firmware-1.5",
    firmware_machine_type="MDT",
)

XTOOL_METALFAB = XtoolDeviceModel(
    model_id="METALFAB",
    name="xTool MetalFab",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_z_axis=True,
    has_drawer=True,
    has_cover_sensor=True,
    has_lid_sensor=True,
    has_camera=True,
    has_fill_light_rest=True,
    has_mode_switch=True,
    has_air_assist_state=True,
    has_gyro=True,
    firmware_content_id="xTool-hj003-firmware",
    firmware_machine_type="MHJ",
)
