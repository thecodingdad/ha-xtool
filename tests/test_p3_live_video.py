"""The P3's live video (from PR #14), through the shared media_stream reader.

Studio's live monitor names the P3's cameras ``far`` and ``upside``; its
photos are taken as ``overview`` and ``closeup``.
"""

from __future__ import annotations

import pytest
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.core import HomeAssistant

from custom_components.xtool.protocols import DEVICE_MODELS

from .conftest import build_coordinator


@pytest.mark.parametrize(("camera", "live_name"), [("overview", "far"), ("closeup", "upside")])
def test_the_live_video_uses_studios_camera_names(
    hass: HomeAssistant, config_entry, camera: str, live_name: str,
) -> None:
    coordinator = build_coordinator(hass, DEVICE_MODELS["P3_V2"])
    entity = next(c for c in coordinator.build_cameras() if c.unique_id.endswith(f"_camera_{camera}"))
    assert entity.supported_features & CameraEntityFeature.STREAM
    assert entity.stream_options == {"use_wallclock_as_timestamps": True}
    entity._live_source._video()
    coordinator.protocol.live_video.assert_called_with(live_name)
