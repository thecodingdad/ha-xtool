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
from functools import lru_cache
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
    with X to the right and Y downwards. A width or height of 0 means
    the whole photo across or down (see ``photo_size_mm``).
    """

    width_mm: float = CALIBRATED_AREA_MM
    height_mm: float = CALIBRATED_AREA_MM
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0


class _SurfaceMap:
    """Where a point of the work surface (mm) is in the raw photo (pixels).

    Inside the calibrated area each grid cell is mapped bilinearly, as
    the calibration says. Outside it, the map continues from the nearest
    edge point along a smooth (cubic) fit of the whole grid, so straight
    lines stay smooth beyond the grid instead of following the small
    differences between the outer cells.
    """

    def __init__(self, calibration: CameraCalibration) -> None:
        self.cal = calibration
        self.cells_x = calibration.grid_w - 1
        self.cells_y = calibration.grid_h - 1
        self.cell_mm_x = CALIBRATED_AREA_MM / self.cells_x
        self.cell_mm_y = (
            CALIBRATED_AREA_MM * calibration.dst_h / calibration.dst_w / self.cells_y
        )
        self.width_mm = CALIBRATED_AREA_MM
        self.height_mm = self.cell_mm_y * self.cells_y
        samples = [
            (col * self.cell_mm_x, row * self.cell_mm_y, calibration.point(row, col))
            for row in range(calibration.grid_h)
            for col in range(calibration.grid_w)
        ]
        rows = [self._terms(u, v) for u, v, _ in samples]
        self._fit_x = _least_squares(rows, [point[0] for _, _, point in samples])
        self._fit_y = _least_squares(rows, [point[1] for _, _, point in samples])

    def _terms(self, u_mm: float, v_mm: float) -> list[float]:
        s = (u_mm - self.width_mm / 2) / (self.width_mm / 2)
        t = (v_mm - self.height_mm / 2) / (self.height_mm / 2)
        if self.cal.grid_w < 4 or self.cal.grid_h < 4:
            return [1.0, s, t, s * t]
        return [1.0, s, t, s * s, s * t, t * t, s ** 3, s * s * t, s * t * t, t ** 3]

    def _fitted(self, u_mm: float, v_mm: float) -> tuple[float, float]:
        terms = self._terms(u_mm, v_mm)
        return (
            sum(c * k for c, k in zip(self._fit_x, terms)),
            sum(c * k for c, k in zip(self._fit_y, terms)),
        )

    def _grid(self, u_mm: float, v_mm: float) -> tuple[float, float]:
        u = u_mm / self.cell_mm_x
        v = v_mm / self.cell_mm_y
        col = min(max(int(u // 1), 0), self.cells_x - 1)
        row = min(max(int(v // 1), 0), self.cells_y - 1)
        a = u - col
        b = v - row
        x00, y00 = self.cal.point(row, col)
        x01, y01 = self.cal.point(row, col + 1)
        x10, y10 = self.cal.point(row + 1, col)
        x11, y11 = self.cal.point(row + 1, col + 1)
        x = (1 - a) * (1 - b) * x00 + a * (1 - b) * x01 + (1 - a) * b * x10 + a * b * x11
        y = (1 - a) * (1 - b) * y00 + a * (1 - b) * y01 + (1 - a) * b * y10 + a * b * y11
        return x, y

    def photo_point(self, u_mm: float, v_mm: float) -> tuple[float, float]:
        """Raw-photo pixel (at the calibration's photo size) of a point."""
        edge_u = min(max(u_mm, 0.0), self.width_mm)
        edge_v = min(max(v_mm, 0.0), self.height_mm)
        x, y = self._grid(edge_u, edge_v)
        if (edge_u, edge_v) != (u_mm, v_mm):
            far_x, far_y = self._fitted(u_mm, v_mm)
            near_x, near_y = self._fitted(edge_u, edge_v)
            x += far_x - near_x
            y += far_y - near_y
        return x, y

    def _in_photo(self, u_mm: float, v_mm: float) -> bool:
        x, y = self.photo_point(u_mm, v_mm)
        return 0 <= x <= self.cal.src_w and 0 <= y <= self.cal.src_h

    def _reach(self, fixed: float, start: float, step: float, across: bool) -> float:
        """How far (whole mm) from ``start`` the photo goes one way."""
        distance = 0
        while distance < 1000:
            nxt = start + (distance + 1) * step
            point = (nxt, fixed) if across else (fixed, nxt)
            if not self._in_photo(*point):
                break
            distance += 1
        return float(distance)

    def photo_size_mm(self) -> tuple[float, float]:
        """The widest area, centred on the calibrated area, that the photo
        fills: across every grid row, and down every grid column."""
        cx = self.width_mm / 2
        cy = self.height_mm / 2
        half_w = min(
            min(self._reach(v, cx, -1, True), self._reach(v, cx, 1, True))
            for v in (row * self.cell_mm_y for row in range(self.cal.grid_h))
        )
        half_h = min(
            min(self._reach(u, cy, -1, False), self._reach(u, cy, 1, False))
            for u in (col * self.cell_mm_x for col in range(self.cal.grid_w))
        )
        return 2 * half_w, 2 * half_h


def _least_squares(rows: list[list[float]], values: list[float]) -> list[float]:
    """Coefficients that best fit ``rows`` x coefficients = ``values``."""
    n = len(rows[0])
    matrix = [
        [sum(r[i] * r[j] for r in rows) for j in range(n)]
        + [sum(r[i] * v for r, v in zip(rows, values))]
        for i in range(n)
    ]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(matrix[r][col]))
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        if abs(matrix[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col:
                factor = matrix[r][col] / matrix[col][col]
                matrix[r] = [a - factor * b for a, b in zip(matrix[r], matrix[col])]
    return [
        matrix[i][n] / matrix[i][i] if abs(matrix[i][i]) >= 1e-12 else 0.0
        for i in range(n)
    ]


@lru_cache(maxsize=4)
def _surface_map(calibration: CameraCalibration) -> _SurfaceMap:
    return _SurfaceMap(calibration)


def photo_size_mm(calibration: CameraCalibration) -> tuple[float, float]:
    """The widest area the photo fills, in whole mm (a crop side of 0).

    Centred on the calibrated area, so no part of it is outside the
    photo across the calibrated rows (width) and columns (height).
    """
    return _surface_map(calibration).photo_size_mm()


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
    if view.width_mm <= 0 or view.height_mm <= 0:
        photo_w, photo_h = photo_size_mm(calibration)
        view = CameraView(
            view.width_mm if view.width_mm > 0 else photo_w,
            view.height_mm if view.height_mm > 0 else photo_h,
            view.center_x_mm,
            view.center_y_mm,
        )
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

    surface = _surface_map(calibration)

    def src(x_mm: float, y_mm: float) -> tuple[float, float]:
        """Photo pixel of a work-surface point."""
        x, y = surface.photo_point(x_mm, y_mm)
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
