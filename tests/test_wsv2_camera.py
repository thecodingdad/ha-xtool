"""Tests for the WS-V2 camera (issue #4).

These lasers have no live video, so the live view is built from photos.
A photo takes about 0.3 s on the F2 Ultra UV.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from .conftest import build_coordinator
from .features import requires

pytestmark = requires("camera_photo_live_view")


def _main_camera(hass: HomeAssistant):
    from custom_components.xtool.protocols import DEVICE_MODELS

    model = next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )
    coordinator = build_coordinator(hass, model)
    coordinator.protocol.camera_snap = AsyncMock(return_value=b"jpeg-1")
    # No calibration: photos are served as taken.
    coordinator.protocol.camera_calibration_file = AsyncMock(return_value=None)
    camera = next(
        c for c in coordinator.build_cameras() if c._camera_name == "main"
    )
    return camera, coordinator.protocol


async def test_camera_is_idle_until_a_live_view_opens(hass: HomeAssistant) -> None:
    camera, _ = _main_camera(hass)
    assert camera.is_streaming is False
    camera._set_live_viewers(1)
    assert camera.is_streaming is True
    camera._set_live_viewers(-1)
    assert camera.is_streaming is False


async def test_recent_photo_is_shared(hass: HomeAssistant) -> None:
    camera, protocol = _main_camera(hass)
    assert await camera.async_camera_image() == b"jpeg-1"
    assert await camera.async_camera_image() == b"jpeg-1"
    assert protocol.camera_snap.await_count == 1


async def test_a_photo_just_taken_answers_the_next_picture(hass: HomeAssistant) -> None:
    """A slow photo (older than MIN_SNAPSHOT_INTERVAL when it arrives) still
    answers the picture request that follows a refresh: no black thumbnail."""
    from .features import has

    if not has("picture_reuse"):
        pytest.skip("branch lacks picture_reuse")
    camera, protocol = _main_camera(hass)
    await camera.async_camera_image()
    camera._last_snapshot_time -= timedelta(seconds=5)
    assert await camera.async_camera_image() == b"jpeg-1"
    assert protocol.camera_snap.await_count == 1
    camera._snapshot_done -= 60  # later requests take a new photo
    camera._last_snapshot_time -= timedelta(seconds=camera._thumbnail_seconds)
    await camera.async_camera_image()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert protocol.camera_snap.await_count == 2


async def test_thumbnail_is_not_older_than_a_few_seconds(hass: HomeAssistant) -> None:
    from custom_components.xtool.protocols.ws_v2.entities import MIN_SNAPSHOT_INTERVAL

    assert MIN_SNAPSHOT_INTERVAL <= timedelta(seconds=10)


async def test_failed_photo_keeps_a_recent_image(hass: HomeAssistant) -> None:
    camera, protocol = _main_camera(hass)
    await camera.async_camera_image()
    camera._last_snapshot_time -= timedelta(seconds=5)
    camera._snapshot_done = float("-inf")
    protocol.camera_snap.return_value = None
    assert await camera.async_camera_image() == b"jpeg-1"


async def test_failed_photo_drops_a_stale_image(hass: HomeAssistant) -> None:
    camera, protocol = _main_camera(hass)
    await camera.async_camera_image()
    camera._last_snapshot_time -= timedelta(minutes=15)
    camera._snapshot_done = float("-inf")
    protocol.camera_snap.side_effect = RuntimeError("offline")
    assert await camera.async_camera_image() is None


@requires("picture_follows_refresh")
async def test_pictures_reuse_a_photo_younger_than_the_refresh(hass: HomeAssistant) -> None:
    """A dashboard picture reloads every few seconds; the refresh sets the photo rate."""
    camera, protocol = _main_camera(hass)
    camera._thumbnail_seconds = 60
    await camera.async_camera_image()
    camera._snapshot_done = float("-inf")
    camera._last_snapshot_time -= timedelta(seconds=30)
    assert await camera.async_camera_image() == b"jpeg-1"
    assert protocol.camera_snap.await_count == 1
    camera._last_snapshot_time -= timedelta(seconds=31)
    await camera.async_camera_image()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert protocol.camera_snap.await_count == 2


@requires("picture_at_once")
async def test_a_picture_does_not_wait_for_a_new_photo(hass: HomeAssistant) -> None:
    """The browser gives up after 10 s; a P2 photo can take 30."""
    camera, protocol = _main_camera(hass)
    camera.hass = hass
    camera._thumbnail_seconds = 60
    await camera.async_camera_image()
    camera._snapshot_done = float("-inf")
    camera._last_snapshot_time -= timedelta(seconds=90)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_photo(name):
        started.set()
        await release.wait()
        return b"jpeg-2"

    protocol.camera_snap.side_effect = slow_photo
    # Answered with the last photo at once; one new photo is coming
    assert await camera.async_camera_image() == b"jpeg-1"
    assert await camera.async_camera_image() == b"jpeg-1"
    await started.wait()
    assert protocol.camera_snap.await_count == 2
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    camera._snapshot_done = float("-inf")
    assert await camera.async_camera_image() == b"jpeg-2"
    assert protocol.camera_snap.await_count == 2


@requires("picture_at_once")
async def test_a_very_old_photo_is_not_shown_while_waiting(hass: HomeAssistant) -> None:
    camera, protocol = _main_camera(hass)
    camera.hass = hass
    camera._thumbnail_seconds = 60
    await camera.async_camera_image()
    camera._snapshot_done = float("-inf")
    camera._last_snapshot_time -= timedelta(minutes=10)
    protocol.camera_snap.return_value = b"jpeg-2"
    assert await camera.async_camera_image() == b"jpeg-2"


def test_live_view_is_faster_than_one_frame_per_second() -> None:
    from custom_components.xtool.protocols.ws_v2.entities import LIVE_FRAME_INTERVAL

    assert LIVE_FRAME_INTERVAL < 1.0


async def test_photos_are_taken_one_at_a_time() -> None:
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    running = 0
    most = 0

    async def snap(name: str) -> bytes:
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.01)
        running -= 1
        return name.encode()

    proto._camera_snap = snap
    proto._connected = True
    results = await asyncio.gather(
        proto.camera_snap("main"), proto.camera_snap("deep"),
    )
    assert results == [b"main", b"deep"]
    assert most == 1


async def test_live_view_ends_when_the_viewer_stops_reading(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.xtool.protocols.ws_v2 import entities

    if not hasattr(entities, "LIVE_WRITE_TIMEOUT"):
        pytest.skip("branch lacks the live write timeout")

    class StuckResponse:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def prepare(self, request) -> None:
            return None

        async def write(self, data: bytes) -> None:
            await asyncio.Event().wait()

    monkeypatch.setattr(entities.web, "StreamResponse", StuckResponse)
    monkeypatch.setattr(entities, "LIVE_WRITE_TIMEOUT", 0.05)
    camera, _ = _main_camera(hass)
    await asyncio.wait_for(camera.handle_async_mjpeg_stream(object()), timeout=2)
    assert camera.is_streaming is False


async def test_photo_age_counts_from_the_request(hass: HomeAssistant) -> None:
    camera, protocol = _main_camera(hass)

    async def slow_snap(name: str) -> bytes:
        await asyncio.sleep(0.05)
        return b"jpeg"

    protocol.camera_snap = AsyncMock(side_effect=slow_snap)
    await camera._image(timedelta(seconds=0.06))
    await asyncio.sleep(0.02)
    await camera._image(timedelta(seconds=0.06))
    assert protocol.camera_snap.await_count == 2
