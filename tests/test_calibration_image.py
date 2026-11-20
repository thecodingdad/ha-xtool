"""The F2 Ultra UV main camera Calibration image.

The raw main camera photo with the laser's calibration grid drawn on
it. Read only: it uses the calibration file the camera already reads.
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from .conftest import SERIAL, build_coordinator
from .features import COMPONENT, requires

pytestmark = requires("calibration_image")


def _f2uv():
    from custom_components.xtool.protocols import DEVICE_MODELS

    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )


def _calibration_file() -> str:
    points = "".join(
        f"[{1000 + col * 100},{500 + row * 100}]" for row in range(21) for col in range(21)
    )
    return json.dumps({
        "grid_num_w": 21, "grid_num_h": 21, "image_src_w": 4000, "image_src_h": 3000,
        "image_dst_w": 2000, "image_dst_h": 2000, "data0": points,
        "calib_time": "2025-11-09 09:41:58",
    })


def _photo() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4000, 3000), (40, 40, 40)).save(out, "JPEG")
    return out.getvalue()


async def test_draws_the_grid_on_the_raw_photo(hass: HomeAssistant) -> None:
    from PIL import Image

    coordinator = build_coordinator(hass, _f2uv())
    coordinator.protocol.camera_calibration_file = AsyncMock(return_value=_calibration_file())
    coordinator.protocol.camera_snap = AsyncMock(return_value=_photo())
    image = coordinator.build_images()[0]
    image.hass = hass
    assert image.unique_id == f"{SERIAL}_camera_main_calibration_image"
    assert image.translation_key == "calibration_image"
    assert image.device_info["identifiers"] == {("xtool", f"{SERIAL}:camera_main")}
    data = await image.async_image()
    result = Image.open(io.BytesIO(data))
    assert result.size == (1000, 750)
    # A grid point (1000, 500 in the 4000 x 3000 photo) is drawn; a spot
    # outside the grid is not.
    r, g, b = result.getpixel((250, 125))
    assert r > 150 or g > 150
    assert max(result.getpixel((50, 700))) < 90
    # Made once: a second request sends nothing.
    await image.async_image()
    coordinator.protocol.camera_snap.assert_awaited_once_with("main")


async def test_nothing_without_a_calibration(hass: HomeAssistant) -> None:
    coordinator = build_coordinator(hass, _f2uv())
    coordinator.protocol.camera_calibration_file = AsyncMock(return_value=None)
    coordinator.protocol.camera_snap = AsyncMock(return_value=_photo())
    image = coordinator.build_images()[0]
    image.hass = hass
    assert await image.async_image() is None
    coordinator.protocol.camera_snap.assert_not_awaited()


def test_only_lasers_with_a_camera_calibration(hass: HomeAssistant) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    for key, model in DEVICE_MODELS.items():
        built = build_coordinator(hass, model).build_images()
        assert bool(built) is bool(getattr(model, "has_camera_calibration", False)), key


def test_image_platform_is_set_up() -> None:
    assert (COMPONENT / "image.py").exists()
    assert "Platform.IMAGE" in (COMPONENT / "__init__.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "path", ["strings.json", "translations/en.json", "translations/de.json"],
)
def test_translated(path: str) -> None:
    images = json.loads((COMPONENT / path).read_text(encoding="utf-8"))["entity"]["image"]
    assert "calibration_image" in images
