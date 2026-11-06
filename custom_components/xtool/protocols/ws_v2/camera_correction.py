"""Straighten camera photos with the laser's own calibration grid.

The F2 Ultra UV stores ``global_calib.json`` on the laser; Studio
downloads it (``downLoadConfig``, file type 3) and straightens every
main-camera photo with it before showing it. The file holds a grid of
``grid_num_w`` x ``grid_num_h`` points (``data0``, ``[x,y]`` pairs in
row order, top row first) in the raw photo (``image_src_w`` x
``image_src_h`` pixels). The grid maps evenly onto the straightened
image (``image_dst_w`` x ``image_dst_h``), so each grid cell is a
quadrilateral in the photo and a rectangle in the result. Mapping
each cell corrects the keystone and the lens distortion together.

Studio's own algorithm (``GS009_CORRECT_IMAGE_WITH_Z``) also takes the
material height; this module uses the calibrated plane only.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from typing import Any

# Width of the straightened image served to Home Assistant. Studio
# makes 2000 x 2000; half of that is plenty for a dashboard and keeps
# each frame fast.
OUTPUT_WIDTH = 1000
JPEG_QUALITY = 85

_POINT = re.compile(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")


@dataclass(frozen=True)
class CameraCalibration:
    """The laser's calibration grid for one camera."""

    grid_w: int
    grid_h: int
    src_w: int
    src_h: int
    dst_w: int
    dst_h: int
    points: tuple[tuple[float, float], ...]

    def point(self, row: int, col: int) -> tuple[float, float]:
        return self.points[row * self.grid_w + col]


def parse_calibration(raw: Any) -> CameraCalibration | None:
    """Parse ``global_calib.json``; return None when it is not usable."""
    try:
        data = raw if isinstance(raw, dict) else json.loads(raw)
        grid_w = int(data["grid_num_w"])
        grid_h = int(data["grid_num_h"])
        points = tuple(
            (float(x), float(y)) for x, y in _POINT.findall(str(data["data0"]))
        )
        calibration = CameraCalibration(
            grid_w=grid_w,
            grid_h=grid_h,
            src_w=int(data["image_src_w"]),
            src_h=int(data["image_src_h"]),
            dst_w=int(data["image_dst_w"]),
            dst_h=int(data["image_dst_h"]),
            points=points,
        )
    except (TypeError, ValueError, KeyError):
        return None
    if (
        grid_w < 2
        or grid_h < 2
        or len(points) != grid_w * grid_h
        or min(calibration.src_w, calibration.src_h,
               calibration.dst_w, calibration.dst_h) <= 0
    ):
        return None
    return calibration


def correct_image(
    jpeg: bytes,
    calibration: CameraCalibration,
    width: int = OUTPUT_WIDTH,
) -> bytes:
    """Return the photo straightened onto the calibrated area, as JPEG.

    Runs in an executor: decoding and mapping take tens of
    milliseconds.
    """
    from PIL import Image  # Home Assistant ships Pillow

    image = Image.open(io.BytesIO(jpeg))
    # Decode at reduced size when the result needs fewer pixels than
    # the photo has (JPEG can skip detail while decoding).
    image.draft("RGB", (image.width // 2, image.height // 2))
    image = image.convert("RGB")
    scale_x = image.width / calibration.src_w
    scale_y = image.height / calibration.src_h
    height = round(width * calibration.dst_h / calibration.dst_w)
    cell_w = width / (calibration.grid_w - 1)
    cell_h = height / (calibration.grid_h - 1)

    def src(row: int, col: int) -> tuple[float, float]:
        x, y = calibration.point(row, col)
        return x * scale_x, y * scale_y

    mesh = []
    for row in range(calibration.grid_h - 1):
        for col in range(calibration.grid_w - 1):
            box = (
                round(col * cell_w),
                round(row * cell_h),
                round((col + 1) * cell_w),
                round((row + 1) * cell_h),
            )
            # Pillow's quad order: upper left, lower left, lower
            # right, upper right.
            quad = (
                *src(row, col),
                *src(row + 1, col),
                *src(row + 1, col + 1),
                *src(row, col + 1),
            )
            mesh.append((box, quad))
    result = image.transform(
        (width, height),
        Image.Transform.MESH,
        mesh,
        Image.Resampling.BILINEAR,
    )
    out = io.BytesIO()
    result.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()
