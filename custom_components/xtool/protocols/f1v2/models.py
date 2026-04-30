"""Model registry entry for the F1 V2 family (firmware 40.51+)."""

from __future__ import annotations

from ..base import XtoolDeviceModel
from .coordinator import F1V2Coordinator
from .protocol import F1V2Protocol

XTOOL_F1_V2 = XtoolDeviceModel(
    model_id="F1 V2",
    name="xTool F1 (firmware 40.51+)",
    protocol_class=F1V2Protocol,
    coordinator_class=F1V2Coordinator,
    has_lid_sensor=True,
    has_machine_lock=True,
    has_purifier_timeout=True,
    firmware_content_id="xTool-f1-firmware",
)
