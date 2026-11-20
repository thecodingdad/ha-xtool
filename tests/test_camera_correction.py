"""Tests for straightening camera photos with the laser calibration (issue #4).

The laser's global_calib.json holds a grid of points in the raw photo
that spreads evenly over the straightened image.
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from .conftest import SERIAL, build_coordinator
from .features import requires

pytestmark = requires("camera_calibration")

# A 3 x 3 grid over the rectangle (100, 75)-(300, 225) of a 400 x 300
# photo, mapped onto a 200 x 200 result.
GRID = {
    "calib_time": "2025-11-09 09:41:58",
    "grid_num_w": 3,
    "grid_num_h": 3,
    "image_src_w": 400,
    "image_src_h": 300,
    "image_dst_w": 200,
    "image_dst_h": 200,
    "thick_num": 1,
    "thick0": 0.0,
    "data0": "[100,75][200,75][300,75][100,150][200,150][300,150][100,225][200,225][300,225]",
}


def _photo() -> bytes:
    """A 400 x 300 photo: red outside the grid, green top half, blue bottom half."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (400, 300), (255, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 75, 300, 149), fill=(0, 255, 0))
    draw.rectangle((100, 150, 300, 225), fill=(0, 0, 255))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=95)
    return out.getvalue()


def test_parse_calibration() -> None:
    from custom_components.xtool.protocols.ws_v2.camera_correction import parse_calibration

    calibration = parse_calibration(json.dumps(GRID).encode())
    assert calibration is not None
    assert calibration.point(0, 0) == (100.0, 75.0)
    assert calibration.point(2, 2) == (300.0, 225.0)


@pytest.mark.parametrize(
    "broken",
    [b"not json", json.dumps({**GRID, "data0": "[1,2]"}).encode(), json.dumps({**GRID, "grid_num_w": 1}).encode()],
)
def test_unusable_calibration_is_ignored(broken: bytes) -> None:
    from custom_components.xtool.protocols.ws_v2.camera_correction import parse_calibration

    assert parse_calibration(broken) is None


def test_photo_is_mapped_onto_the_grid() -> None:
    from PIL import Image

    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        correct_image,
        parse_calibration,
    )

    calibration = parse_calibration(json.dumps(GRID))
    result = Image.open(io.BytesIO(correct_image(_photo(), calibration)))
    # The default view is the calibrated area at 5 pixels per mm.
    assert result.size == (1000, 1000)
    top = result.getpixel((500, 200))
    bottom = result.getpixel((500, 800))
    assert top[1] > 200 and top[0] < 60 and top[2] < 60
    assert bottom[2] > 200 and bottom[0] < 60 and bottom[1] < 60
    # Nothing from outside the grid (red) reaches the result.
    corner = result.getpixel((20, 20))
    assert corner[0] < 100


async def test_main_camera_serves_straightened_photos(hass: HomeAssistant) -> None:
    from PIL import Image

    from custom_components.xtool.protocols import DEVICE_MODELS

    model = next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )
    coordinator = build_coordinator(hass, model)
    coordinator.protocol.camera_snap = AsyncMock(return_value=_photo())
    coordinator.protocol.camera_calibration_file = AsyncMock(
        return_value=json.dumps(GRID).encode(),
    )
    cameras = {c._camera_name: c for c in coordinator.build_cameras()}
    for camera in cameras.values():
        camera.hass = hass
    main = Image.open(io.BytesIO(await cameras["main"].async_camera_image()))
    assert main.size[0] == main.size[1]
    assert coordinator.protocol.camera_calibration_file.await_count == 1


async def test_without_calibration_photos_are_raw(hass: HomeAssistant) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    model = next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )
    coordinator = build_coordinator(hass, model)
    photo = _photo()
    coordinator.protocol.camera_snap = AsyncMock(return_value=photo)
    coordinator.protocol.camera_calibration_file = AsyncMock(return_value=None)
    main = next(c for c in coordinator.build_cameras() if c._camera_name == "main")
    main.hass = hass
    assert await main.async_camera_image() == photo


def test_view_moves_and_widens_the_area() -> None:
    from PIL import Image

    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        CameraView,
        correct_image,
        parse_calibration,
    )

    calibration = parse_calibration(json.dumps(GRID))
    # The top half of the calibrated area only: all green.
    top = Image.open(io.BytesIO(correct_image(
        _photo(), calibration, CameraView(200, 100, 0, -50),
    )))
    assert top.size == (1000, 500)
    pixel = top.getpixel((500, 250))
    assert pixel[1] > 200 and pixel[2] < 60
    # Wider than the calibrated area: the sides come from outside the grid (red).
    wide = Image.open(io.BytesIO(correct_image(
        _photo(), calibration, CameraView(300, 200, 0, 0),
    )))
    assert wide.size == (1500, 1000)
    side = wide.getpixel((20, 500))
    assert side[0] > 200 and side[1] < 60


def test_deep_perspective_fills_the_result() -> None:
    """The deep camera's correction gives 1000 x 750 with no empty corners, from a full or half-size photo."""
    from PIL import Image

    from custom_components.xtool.protocols.ws_v2.camera_correction import perspective_image
    from custom_components.xtool.protocols.ws_v2.models import F2_ULTRA_UV_DEEP_PERSPECTIVE

    _, coefficients, source, size = F2_ULTRA_UV_DEEP_PERSPECTIVE
    for width, height in (source, (source[0] // 2, source[1] // 2)):
        photo = Image.new("RGB", (width, height), (255, 255, 255))
        out = io.BytesIO()
        photo.save(out, "JPEG")
        result = Image.open(io.BytesIO(perspective_image(out.getvalue(), coefficients, source, size)))
        assert result.size == (1000, 750)
        for corner in ((2, 2), (997, 2), (2, 747), (997, 747)):
            assert min(result.getpixel(corner)) > 200


def test_view_numbers_for_the_main_camera(hass: HomeAssistant) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    model = next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )
    coordinator = build_coordinator(hass, model)
    ids = {n.unique_id for n in coordinator.build_numbers()}
    assert {
        f"{SERIAL}_camera_main_view_{setting}"
        for setting in ("width", "height", "center_x", "center_y")
    } <= ids


async def test_deep_camera_is_corrected(hass: HomeAssistant) -> None:
    from PIL import Image

    from custom_components.xtool.protocols import DEVICE_MODELS

    model = next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )
    coordinator = build_coordinator(hass, model)
    coordinator.protocol.camera_snap = AsyncMock(return_value=_photo())
    coordinator.protocol.camera_calibration_file = AsyncMock(return_value=None)
    deep = next(c for c in coordinator.build_cameras() if c._camera_name == "deep")
    deep.hass = hass
    from custom_components.xtool.protocols.ws_v2 import entities

    if hasattr(entities, "PERSPECTIVE_STRAIGHTENED"):
        # The Perspective select (default Rotated) chooses the correction.
        entities._perspective_settings(coordinator)["deep"] = entities.PERSPECTIVE_STRAIGHTENED
    image = Image.open(io.BytesIO(await deep.async_camera_image()))
    assert image.size == (1000, 750)
