"""Model registry entries for the xTool S1 family (only one model)."""

from __future__ import annotations

from ..base import XtoolDeviceModel
from .coordinator import S1Coordinator
from .protocol import S1Protocol

XTOOL_S1 = XtoolDeviceModel(
    model_id="S1",
    name="xTool S1",
    protocol_class=S1Protocol,
    coordinator_class=S1Coordinator,
    has_z_axis=True,
    firmware_content_id="xTool-d2-firmware",
    firmware_multi_package=True,
    firmware_board_ids=("xTool-d2-0x20", "xTool-d2-0x21", "xTool-d2-0x22"),
)
