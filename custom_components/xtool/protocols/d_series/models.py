"""Model registry entries for the D-series family."""

from __future__ import annotations

from ..base import XtoolDeviceModel
from .coordinator import DSeriesCoordinator
from .protocol import DSeriesProtocol

XTOOL_D1 = XtoolDeviceModel(
    model_id="D1",
    name="xTool D1",
    protocol_class=DSeriesProtocol,
    coordinator_class=DSeriesCoordinator,
    has_smoking_fan=False,
    has_tilt_sensor=True,
    has_moving_sensor=True,
    has_limit_switch=True,
    firmware_content_id="xTool-d1-firmware",
)

XTOOL_D1_PRO = XtoolDeviceModel(
    model_id="D1Pro",
    name="xTool D1 Pro",
    protocol_class=DSeriesProtocol,
    coordinator_class=DSeriesCoordinator,
    has_tilt_sensor=True,
    has_moving_sensor=True,
    has_limit_switch=True,
    firmware_content_id="xTool-d1pro-firmware",
)

XTOOL_D1_PRO_2_0 = XtoolDeviceModel(
    model_id="D1Pro 2.0",
    name="xTool D1 Pro 2.0",
    protocol_class=DSeriesProtocol,
    coordinator_class=DSeriesCoordinator,
    has_tilt_sensor=True,
    has_moving_sensor=True,
    has_limit_switch=True,
    # xTool Studio bundles a separate firmware archive for D1 Pro 2.0
    # (`xTool-d1pro-firmware-2.0`); the legacy `xcs-d1pro-firmware` ID
    # used by older XCS Android resolves to D1 Pro 1.0 binaries instead.
    firmware_content_id="xTool-d1pro-firmware-2.0",
)
