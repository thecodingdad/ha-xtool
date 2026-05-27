"""xTool M2 device-model entry."""

from __future__ import annotations

from ..base import XtoolDeviceModel
from .coordinator import M2Coordinator
from .protocol import M2WSV2Protocol


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
XTOOL_M2 = XtoolDeviceModel(
    model_id="M2",
    name="xTool M2",
    protocol_class=M2WSV2Protocol,
    coordinator_class=M2Coordinator,
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
    firmware_content_id="xTool-m2-firmware",
    firmware_machine_type="JS002",
    # Studio's xcs-extension manifest classifies M2 as
    # protocolVersion:"V2", channelType:"socket" — i.e. the
    # standard V2 multi-channel WS framework on port 28900.
    protocol_version="V2",
    discovery_match=("JS002", "M2"),
)
