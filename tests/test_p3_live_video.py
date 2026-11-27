"""The P3's cameras as Studio and the P3's firmware (40.23.012) have them.

The firmware lists ``far`` (overview), ``near`` (close), ``upside`` and
``downside``. Studio's live monitor shows ``far`` (front view) and
``upside`` (side view); it takes photos of ``near`` and ``upside`` with
``GET /v1/camera/image`` and ``data:{stream}`` (cameraNearSnap /
cameraUpsideSnap), and of ``far`` only through its measuring algorithm.
``/v1/camera/snap`` answers ``code -1`` on the P3 (#87).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.core import HomeAssistant

from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.base import XtoolDeviceState
from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

from .conftest import build_coordinator

P3 = DEVICE_MODELS["P3_V2"]


def _camera(hass: HomeAssistant, name: str):
    coordinator = build_coordinator(hass, P3)
    return coordinator, next(c for c in coordinator.build_cameras() if c.unique_id.endswith(f"_camera_{name}"))


@pytest.mark.parametrize(("camera", "live_name"), [("overview", "far"), ("side", "upside")])
def test_the_live_video_uses_studios_camera_names(
    hass: HomeAssistant, config_entry, camera: str, live_name: str,
) -> None:
    coordinator, entity = _camera(hass, camera)
    assert entity.supported_features & CameraEntityFeature.STREAM
    assert entity.stream_options == {"use_wallclock_as_timestamps": True}
    entity._live_source._video()
    coordinator.protocol.live_video.assert_called_with(live_name)


def test_the_close_camera_has_no_live_video(hass: HomeAssistant, config_entry) -> None:
    _, entity = _camera(hass, "closeup")
    assert not entity.supported_features & CameraEntityFeature.STREAM
    assert entity._live_source is None


@pytest.mark.parametrize(("camera", "stills_from_live"), [("overview", True), ("side", False), ("closeup", False)])
def test_only_the_overview_shows_a_frame_of_its_video(
    hass: HomeAssistant, config_entry, camera: str, stills_from_live: bool,
) -> None:
    coordinator, entity = _camera(hass, camera)
    coordinator.protocol.live_camera = None
    assert entity.use_stream_for_stills is stills_from_live


@pytest.mark.parametrize(("camera", "stream"), [("closeup", "near"), ("side", "upside")])
async def test_photos_as_studio_takes_them(camera: str, stream: str) -> None:
    protocol = WSV2Protocol("192.0.2.30")
    protocol.set_model(P3)
    protocol.request = AsyncMock(return_value={"filename": "snap.jpg"})
    protocol._download_file_stream = AsyncMock(return_value=b"jpeg")
    assert await protocol._camera_snap(camera) == b"jpeg"
    protocol.request.assert_awaited_once_with("/v1/camera/image", "GET", data={"stream": stream}, timeout=15.0)
    protocol._download_file_stream.assert_awaited_once_with("snap.jpg", file_type=5)


async def test_the_laser_position_is_read_with_put() -> None:
    """Studio's P3 getLaserPosition; a GET answers code -3 (#87)."""
    protocol = WSV2Protocol("192.0.2.30")
    protocol.set_model(P3)
    calls: list[tuple] = []

    async def request(url, method="GET", params=None, data=None, **_):
        calls.append((url, method, params, data))
        return {"x": 10.0, "y": 20.0, "z": 3.0} if params == {"type": "laser_head"} else {}

    protocol.request = request
    protocol._connected = True
    await protocol.poll_state(XtoolDeviceState())
    laser = [c for c in calls if c[2] == {"type": "laser_head"}]
    assert laser == [("/v1/peripheral/param", "PUT", {"type": "laser_head"}, {"action": "get_coord", "waitTime": 3000})]


async def test_the_overview_asks_for_no_photo() -> None:
    protocol = WSV2Protocol("192.0.2.30")
    protocol.set_model(P3)
    protocol.request = AsyncMock()
    assert await protocol._camera_snap("overview") is None
    protocol.request.assert_not_awaited()


async def test_a_camera_taken_over_stops_its_stream(hass: HomeAssistant, config_entry) -> None:
    """Home Assistant's stream would open it again and again (#87); opening it again takes over."""
    _, entity = _camera(hass, "side")
    entity.hass = hass
    entity.stream = AsyncMock()
    entity._live_taken_over()
    await hass.async_block_till_done()
    entity.stream.stop.assert_awaited_once()


@pytest.mark.parametrize(("live", "stills_from_live"), [(None, True), ("far", True), ("upside", False)])
def test_the_overview_takes_no_still_from_another_cameras_video(
    hass: HomeAssistant, config_entry, live: str | None, stills_from_live: bool,
) -> None:
    """A still starts the stream; it must not take the video from the Side camera being watched."""
    coordinator, entity = _camera(hass, "overview")
    coordinator.protocol.live_camera = live
    assert entity.use_stream_for_stills is stills_from_live
