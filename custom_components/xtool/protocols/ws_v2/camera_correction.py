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

# Width of a turned (uncalibrated) photo served to Home Assistant.
OUTPUT_WIDTH = 1000
# Size of the calibrated area on the work surface. Measured on an F2
# Ultra UV: the straightened image shows about 200 mm of a cutting
# mat across. Studio's own algorithm is native code, so this is not
# read from the laser.
CALIBRATED_AREA_MM = 200.0
# Resolution of a straightened image (Studio makes 2000 x 2000 for the
# calibrated area; half of that is plenty for a dashboard and keeps
# each frame fast), and its largest side.
PIXELS_PER_MM = 5.0
MAX_OUTPUT_SIDE = 1600
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


@dataclass(frozen=True)
class CameraView:
    """The area of the work surface to show, in millimetres.

    The centre is measured from the centre of the calibrated area,
    with X to the right and Y downwards.
    """

    width_mm: float = CALIBRATED_AREA_MM
    height_mm: float = CALIBRATED_AREA_MM
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0


def correct_image(
    jpeg: bytes,
    calibration: CameraCalibration,
    view: CameraView | None = None,
) -> bytes:
    """Return the photo straightened onto the chosen area, as JPEG.

    Inside the calibrated area each grid cell is mapped exactly; outside
    it the nearest cell is extended, which is less accurate. Runs in an
    executor: decoding and mapping take tens of milliseconds.
    """
    from PIL import Image  # Home Assistant ships Pillow

    view = view or CameraView()
    image = Image.open(io.BytesIO(jpeg))
    # Decode at reduced size when the result needs fewer pixels than
    # the photo has (JPEG can skip detail while decoding).
    image.draft("RGB", (image.width // 2, image.height // 2))
    image = image.convert("RGB")
    scale_x = image.width / calibration.src_w
    scale_y = image.height / calibration.src_h
    cells_x = calibration.grid_w - 1
    cells_y = calibration.grid_h - 1
    # Millimetres per grid cell across and down.
    cell_mm_x = CALIBRATED_AREA_MM / cells_x
    cell_mm_y = CALIBRATED_AREA_MM * calibration.dst_h / calibration.dst_w / cells_y
    area_h_mm = cell_mm_y * cells_y

    px_per_mm = min(
        PIXELS_PER_MM, MAX_OUTPUT_SIDE / max(view.width_mm, view.height_mm),
    )
    width = max(1, round(view.width_mm * px_per_mm))
    height = max(1, round(view.height_mm * px_per_mm))
    left_mm = CALIBRATED_AREA_MM / 2 + view.center_x_mm - view.width_mm / 2
    top_mm = area_h_mm / 2 + view.center_y_mm - view.height_mm / 2

    def src(x_mm: float, y_mm: float) -> tuple[float, float]:
        """Photo pixel of a work-surface point (bilinear in its cell)."""
        u = x_mm / cell_mm_x
        v = y_mm / cell_mm_y
        col = min(max(int(u // 1), 0), cells_x - 1)
        row = min(max(int(v // 1), 0), cells_y - 1)
        a = u - col
        b = v - row
        x00, y00 = calibration.point(row, col)
        x01, y01 = calibration.point(row, col + 1)
        x10, y10 = calibration.point(row + 1, col)
        x11, y11 = calibration.point(row + 1, col + 1)
        x = (1 - a) * (1 - b) * x00 + a * (1 - b) * x01 + (1 - a) * b * x10 + a * b * x11
        y = (1 - a) * (1 - b) * y00 + a * (1 - b) * y01 + (1 - a) * b * y10 + a * b * y11
        return x * scale_x, y * scale_y

    # Mesh boxes of about one grid cell, aligned with the grid.
    def edges(start_mm: float, size_mm: float, cell_mm: float, pixels: int) -> list[tuple[float, int]]:
        marks = [start_mm]
        k = int(start_mm // cell_mm) + 1
        while k * cell_mm < start_mm + size_mm:
            marks.append(k * cell_mm)
            k += 1
        marks.append(start_mm + size_mm)
        return [(m, round((m - start_mm) / size_mm * pixels)) for m in marks]

    xs = edges(left_mm, view.width_mm, cell_mm_x, width)
    ys = edges(top_mm, view.height_mm, cell_mm_y, height)
    mesh = []
    for (y0_mm, y0), (y1_mm, y1) in zip(ys, ys[1:]):
        if y1 <= y0:
            continue
        for (x0_mm, x0), (x1_mm, x1) in zip(xs, xs[1:]):
            if x1 <= x0:
                continue
            # Pillow's quad order: upper left, lower left, lower
            # right, upper right.
            quad = (
                *src(x0_mm, y0_mm),
                *src(x0_mm, y1_mm),
                *src(x1_mm, y1_mm),
                *src(x1_mm, y0_mm),
            )
            mesh.append(((x0, y0, x1, y1), quad))
    result = image.transform(
        (width, height),
        Image.Transform.MESH,
        mesh,
        Image.Resampling.BILINEAR,
    )
    out = io.BytesIO()
    result.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def perspective_image(
    jpeg: bytes,
    coefficients: tuple[float, ...],
    source_size: tuple[int, int],
    output_size: tuple[int, int],
) -> bytes:
    """Return the photo with a fixed perspective correction, as JPEG.

    ``coefficients`` are the eight perspective values Pillow uses: they
    map a point of the result to a point of a ``source_size`` photo.
    They were measured once on a cutting mat, so its grid comes out
    straight, square and upright. A photo of another size is scaled.
    """
    from PIL import Image  # Home Assistant ships Pillow

    image = Image.open(io.BytesIO(jpeg))
    image.draft("RGB", (image.width // 2, image.height // 2))
    image = image.convert("RGB")
    sx = image.width / source_size[0]
    sy = image.height / source_size[1]
    a, b, c, d, e, f, g, h = coefficients
    scaled = (a * sx, b * sx, c * sx, d * sy, e * sy, f * sy, g, h)
    result = image.transform(
        output_size, Image.Transform.PERSPECTIVE, scaled, Image.Resampling.BICUBIC,
    )
    out = io.BytesIO()
    result.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()
