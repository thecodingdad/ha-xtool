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
    firmware_content_id="xcs-ext-m1",
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
    firmware_content_id="xcs-ext-m1-lite",
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
    firmware_content_id="xcs-ext-p2",
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
    firmware_content_id="xcs-ext-p2s",
    firmware_machine_type="MXP",
)

XTOOL_P1 = XtoolDeviceModel(
    model_id="P1",
    name="xTool Laserbox",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    firmware_content_id="xcs-ext-p1",
)

XTOOL_F1 = XtoolDeviceModel(
    model_id="F1",
    name="xTool F1",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    has_camera_exposure=True,
    has_laser_head_position=True,
    has_fill_light_rest=True,
    has_mode_switch=True,
    firmware_content_id="xcs-ext-f1",
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
    has_mode_switch=True,
    firmware_content_id="xcs-ext-f1-ultra",
)

XTOOL_GS005 = XtoolDeviceModel(
    model_id="GS005",
    name="xTool F1 Lite",
    protocol_class=RestProtocol,
    coordinator_class=RestCoordinator,
    firmware_content_id="xcs-ext-gs005",
)
