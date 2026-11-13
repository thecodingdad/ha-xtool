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
    # Width of the calibrated area on the work surface
    area_mm: float = CALIBRATED_AREA_MM
    # The grid's cells are square (the P2's are 10 mm), even when the
    # straightened image is not a whole number of cells tall
    square_cells: bool = False
    # Height of the calibrated plane above the bed (the P2: thick0, 3 mm)
    plane_mm: float = 0.0

    @property
    def area_h_mm(self) -> float:
        return self.area_mm * self.dst_h / self.dst_w

    @property
    def cell_mm(self) -> tuple[float, float]:
        """Millimetres per grid cell, across and down."""
        across = self.area_mm / (self.grid_w - 1)
        if self.square_cells:
            return across, across
        return across, self.area_h_mm / (self.grid_h - 1)

    def point(self, row: int, col: int) -> tuple[float, float]:
        return self.points[row * self.grid_w + col]


def parse_calibration(
    raw: Any, pixels_per_mm: float | None = None,
) -> CameraCalibration | None:
    """Parse a calibration file; return None when it is not usable.

    ``pixels_per_mm`` is the straightened image's scale, when the laser's
    Studio extension names it (the P2: 4 for the overview camera, 20 for
    the close camera); the calibrated area is then ``image_dst_w`` / it.
    Without it the area is the F2 Ultra UV's measured 200 mm.
    """
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
            area_mm=(
                int(data["image_dst_w"]) / pixels_per_mm
                if pixels_per_mm else CALIBRATED_AREA_MM
            ),
            square_cells=bool(pixels_per_mm),
            plane_mm=float(data.get("thick0") or 0.0),
        )
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
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


@dataclass(frozen=True)
class SurfaceLift:
    """A material surface above (or below) the calibrated plane.

    A camera sees a raised surface larger and shifted away from the point
    straight below it. The camera's position is in the calibrated area's
    millimetres (x, y of the point below it, and its height above the
    calibrated plane). ``surface_mm`` is the surface's height above the
    calibrated plane.
    """

    camera_x_mm: float
    camera_y_mm: float
    camera_height_mm: float
    surface_mm: float = 0.0

    def seen_at(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        """Where on the calibrated plane a point of the surface appears."""
        scale = self.camera_height_mm / max(self.camera_height_mm - self.surface_mm, 1.0)
        return (
            self.camera_x_mm + (x_mm - self.camera_x_mm) * scale,
            self.camera_y_mm + (y_mm - self.camera_y_mm) * scale,
        )


@dataclass(frozen=True)
class Overlay:
    """Shapes drawn on a straightened picture, in the calibrated area's mm.

    ``areas``: (x, y, width, height, colour), an outline with a dot at its
    origin (top left) and one at its centre, all in its colour.
    ``lines``: (x0, y0, x1, y1, label), the label written at the lower
    end. ``markers``: (x, y), a red target. ``spots``: (x, y, length,
    width, angle), an outline whose inside is that size (mm), long along
    ``angle`` (degrees from X toward Y), drawn 3 px wide outward like a ring. ``rings``: (x, y, diameter), a circle whose inside is that
    diameter (mm), drawn 3 px wide outward, so a dot that size sits just
    inside it. ``images``: (x, y, width, height, JPEG), a picture laid on
    that area, under everything else. The colours are the lines', the
    markers' and the spots' (also the rings').
    """

    areas: tuple[tuple[float, float, float, float, tuple[int, int, int]], ...] = ()
    lines: tuple[tuple[float, float, float, float, str], ...] = ()
    markers: tuple[tuple[float, float], ...] = ()
    spots: tuple[tuple[float, float, float, float, float], ...] = ()
    images: tuple[tuple[float, float, float, float, bytes], ...] = ()
    rings: tuple[tuple[float, float, float], ...] = ()
    line_color: tuple[int, int, int] = (0, 200, 255)
    marker_color: tuple[int, int, int] = (255, 40, 40)
    spot_color: tuple[int, int, int] = (255, 30, 30)


OVERLAY_LABEL_BACKGROUND = (0, 0, 0)
OVERLAY_RING_WIDTH = 3


def _draw_overlay(
    image: Any, overlay: Overlay, left_mm: float, top_mm: float, px_per_mm: float,
) -> None:
    from PIL import Image, ImageDraw, ImageFont  # Home Assistant ships Pillow

    draw = ImageDraw.Draw(image)
    width = max(2, round(image.width / 500))

    def px(x_mm: float, y_mm: float) -> tuple[float, float]:
        return (x_mm - left_mm) * px_per_mm, (y_mm - top_mm) * px_per_mm

    for x, y, w, h, jpeg in overlay.images:
        (a, b), (c, d) = px(x, y), px(x + w, y + h)
        size = (round(c - a), round(d - b))
        if size[0] < 1 or size[1] < 1:
            continue
        try:
            picture = Image.open(io.BytesIO(jpeg)).convert("RGB").resize(
                size, Image.Resampling.BILINEAR,
            )
        except Exception:  # noqa: BLE001 - a bad picture is left out
            continue
        image.paste(picture, (round(a), round(b)))
    for x, y, length, breadth, angle in overlay.spots:
        # An ellipse's outline, turned: the dot itself stays visible inside
        cx, cy = px(x, y)
        a = length * px_per_mm / 2 + OVERLAY_RING_WIDTH
        b = breadth * px_per_mm / 2 + OVERLAY_RING_WIDTH
        size = int(2 * max(a, b)) + 4
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse(
            (size / 2 - a, size / 2 - b, size / 2 + a, size / 2 + b),
            outline=255, width=OVERLAY_RING_WIDTH,
        )
        # Pillow turns counterclockwise on screen; Y points down
        mask = mask.rotate(-angle, resample=Image.Resampling.BICUBIC)
        x0, y0 = round(cx - size / 2), round(cy - size / 2)
        image.paste(overlay.spot_color, (x0, y0, x0 + size, y0 + size), mask)
    for x, y, diameter in overlay.rings:
        # Pillow draws an outline inward from the box: the box is the
        # outside, 3 px beyond the inside diameter
        cx, cy = px(x, y)
        r = diameter * px_per_mm / 2 + OVERLAY_RING_WIDTH
        draw.ellipse(
            (cx - r, cy - r, cx + r, cy + r),
            outline=overlay.spot_color, width=OVERLAY_RING_WIDTH,
        )

    # Labels as large as the space between lines allows: white on black
    label_xs = sorted(px(x1, y1)[0] for _, _, x1, y1, label in overlay.lines if label)
    spacing = min((b - a for a, b in zip(label_xs, label_xs[1:]) if b > a), default=image.width)
    size = round(min(max(12.0, min(image.height / 25, spacing * 0.8)), 64.0))
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:  # Pillow before 10.1
        font = ImageFont.load_default()
    labels = []
    for x0, y0, x1, y1, label in overlay.lines:
        start, end = px(x0, y0), px(x1, y1)
        draw.line((start, end), fill=overlay.line_color, width=max(1, width // 2))
        if label and -size < end[0] < image.width + size:
            labels.append(((end[0], min(end[1], image.height) - 2), label))
    for at, label in labels:
        box = draw.textbbox(at, label, font=font, anchor="md")
        pad = max(2, size // 8)
        draw.rectangle(
            (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad),
            fill=OVERLAY_LABEL_BACKGROUND,
        )
        # Bold: the letters drawn with an outline of their own colour
        draw.text(
            at, label, fill=(255, 255, 255), font=font, anchor="md",
            stroke_width=max(1, size // 14), stroke_fill=(255, 255, 255),
        )
    dot = max(4.0, 1.2 * px_per_mm)
    for x, y, w, h, color in overlay.areas:
        (a, b), (c, d) = px(x, y), px(x + w, y + h)
        draw.rectangle((a, b, c, d), outline=color, width=width)
        for cx, cy in ((a, b), ((a + c) / 2, (b + d) / 2)):
            draw.ellipse(
                (cx - dot, cy - dot, cx + dot, cy + dot), fill=color, outline=(0, 0, 0),
                width=max(1, width // 2),
            )
    for x, y in overlay.markers:
        cx, cy = px(x, y)
        r = max(6.0, 2.5 * px_per_mm)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=overlay.marker_color, width=width)
        draw.line((cx - 2 * r, cy, cx + 2 * r, cy), fill=overlay.marker_color, width=max(1, width // 2))
        draw.line((cx, cy - 2 * r, cx, cy + 2 * r), fill=overlay.marker_color, width=max(1, width // 2))


# A camera seen as a lens: rotation (3), translation (3), focal length,
# aspect, centre (2) and four fisheye terms, mapping work-surface mm to
# photo pixels (the OpenCV fisheye model).
LENS_PARAMETERS = 14


def _rotate(rotation: Any, points: Any) -> Any:
    import numpy as np  # Home Assistant ships numpy

    angle = float(np.linalg.norm(rotation))
    if angle < 1e-12:
        return points
    axis = rotation / angle
    cos, sin = np.cos(angle), np.sin(angle)
    return (
        points * cos
        + np.cross(axis, points) * sin
        + np.outer(points @ axis, axis) * (1 - cos)
    )


def _lens_project(params: Any, mm: Any) -> Any:
    """Photo pixels of work-surface points (N x 2 mm) through a lens."""
    import numpy as np

    points = np.column_stack([mm, np.zeros(len(mm))])
    seen = _rotate(params[0:3], points) + params[3:6]
    x, y = seen[:, 0] / seen[:, 2], seen[:, 1] / seen[:, 2]
    r = np.hypot(x, y)
    theta = np.arctan(r)
    k = params[10:14]
    theta_d = theta * (
        1 + k[0] * theta**2 + k[1] * theta**4 + k[2] * theta**6 + k[3] * theta**8
    )
    scale = theta_d / np.maximum(r, 1e-12)
    focal = params[6]
    return np.column_stack([
        focal * scale * x + params[8], focal * params[7] * scale * y + params[9],
    ])


def _fit_lens(mm: Any, pixels: Any, guess: tuple[float, ...]) -> Any:
    """The lens that best maps the grid's points onto the photo.

    Levenberg-Marquardt from a guess (a camera of the same model): the
    calibration grid is only exact inside itself; the lens continues it
    beyond the grid as the camera really sees.
    """
    import numpy as np

    params = np.array(guess, dtype=float)
    target = pixels.ravel()

    def residual(p: Any) -> Any:
        return _lens_project(p, mm).ravel() - target

    current = residual(params)
    damping = 1e-3
    for _ in range(60):
        jacobian = np.empty((len(current), len(params)))
        for i in range(len(params)):
            step = 1e-6 * max(1.0, abs(params[i]))
            shifted = params.copy()
            shifted[i] += step
            jacobian[:, i] = (residual(shifted) - current) / step
        normal = jacobian.T @ jacobian
        gradient = jacobian.T @ current
        while True:
            change = np.linalg.solve(
                normal + damping * np.diag(np.diag(normal)), -gradient,
            )
            trial = residual(params + change)
            if trial @ trial < current @ current:
                params, current = params + change, trial
                damping = max(damping / 3, 1e-9)
                break
            damping *= 4
            if damping > 1e9:
                return params
        if np.linalg.norm(change) < 1e-9 * max(1.0, np.linalg.norm(params)):
            break
    return params


class _SurfaceMap:
    """Where a point of the work surface (mm) is in the raw photo (pixels).

    Inside the calibrated area each grid cell is mapped bilinearly, as
    the calibration says. Outside it, the map continues from the nearest
    edge point along a smooth fit of the whole grid, so straight lines
    stay smooth beyond the grid instead of following the small
    differences between the outer cells. With a lens guess the fit is the
    camera's lens, which stays true much further beyond the grid than a
    cubic fit does.
    """

    def __init__(
        self, calibration: CameraCalibration, lens: tuple[float, ...] | None = None,
    ) -> None:
        self.cal = calibration
        self.cells_x = calibration.grid_w - 1
        self.cells_y = calibration.grid_h - 1
        self.cell_mm_x, self.cell_mm_y = calibration.cell_mm
        self.width_mm = calibration.area_mm
        self.height_mm = self.cell_mm_y * self.cells_y
        samples = [
            (col * self.cell_mm_x, row * self.cell_mm_y, calibration.point(row, col))
            for row in range(calibration.grid_h)
            for col in range(calibration.grid_w)
        ]
        rows = [self._terms(u, v) for u, v, _ in samples]
        self._fit_x = _least_squares(rows, [point[0] for _, _, point in samples])
        self._fit_y = _least_squares(rows, [point[1] for _, _, point in samples])
        self._lens = None
        if lens is not None and len(lens) == LENS_PARAMETERS:
            import numpy as np

            mm = np.array([(u, v) for u, v, _ in samples])
            pixels = np.array([point for _, _, point in samples])
            fitted = _fit_lens(mm, pixels, lens)
            error = np.hypot(*(_lens_project(fitted, mm) - pixels).T)
            # A lens that does not fit the grid is not used
            if np.isfinite(error).all() and float(np.median(error)) < 0.02 * calibration.src_w:
                self._lens = fitted

    def _terms(self, u_mm: float, v_mm: float) -> list[float]:
        s = (u_mm - self.width_mm / 2) / (self.width_mm / 2)
        t = (v_mm - self.height_mm / 2) / (self.height_mm / 2)
        if self.cal.grid_w < 4 or self.cal.grid_h < 4:
            return [1.0, s, t, s * t]
        return [1.0, s, t, s * s, s * t, t * t, s ** 3, s * s * t, s * t * t, t ** 3]

    def _fitted(self, u_mm: float, v_mm: float) -> tuple[float, float]:
        if self._lens is not None:
            import numpy as np

            x, y = _lens_project(self._lens, np.array([[u_mm, v_mm]]))[0]
            return float(x), float(y)
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

    def photo_points(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """``photo_point`` of many points; the lens is evaluated in one go."""
        if self._lens is None:
            return [self.photo_point(u, v) for u, v in points]
        import numpy as np

        result: list[tuple[float, float]] = []
        outside: list[int] = []
        far: list[tuple[float, float]] = []
        near: list[tuple[float, float]] = []
        for u_mm, v_mm in points:
            edge_u = min(max(u_mm, 0.0), self.width_mm)
            edge_v = min(max(v_mm, 0.0), self.height_mm)
            result.append(self._grid(edge_u, edge_v))
            if (edge_u, edge_v) != (u_mm, v_mm):
                outside.append(len(result) - 1)
                far.append((u_mm, v_mm))
                near.append((edge_u, edge_v))
        if outside:
            shift = _lens_project(self._lens, np.array(far)) - _lens_project(self._lens, np.array(near))
            for index, (dx, dy) in zip(outside, shift):
                x, y = result[index]
                result[index] = (x + float(dx), y + float(dy))
        return result

    def _in_photo(self, u_mm: float, v_mm: float) -> bool:
        x, y = self.photo_point(u_mm, v_mm)
        return 0 <= x <= self.cal.src_w and 0 <= y <= self.cal.src_h

    def _reach(self, fixed: float, start: float, step: float, across: bool) -> float:
        """How far (whole mm, up to 1000) from ``start`` the photo goes one
        way: a binary search, as the photo has no holes along a line."""

        def inside(distance: int) -> bool:
            nxt = start + distance * step
            return self._in_photo(*((nxt, fixed) if across else (fixed, nxt)))

        low, high = 0, 1000  # inside(low) holds; past high is not searched
        if inside(high):
            return float(high)
        while high - low > 1:
            middle = (low + high) // 2
            if inside(middle):
                low = middle
            else:
                high = middle
        return float(low)

    def photo_size_mm(self) -> tuple[float, float]:
        """The widest area, centred on the calibrated area, that the photo
        fills: across every grid row, and down every grid column."""
        size = getattr(self, "_photo_size", None)
        if size is None:
            size = self._photo_size = self._measure_photo_size()
        return size

    def _measure_photo_size(self) -> tuple[float, float]:
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
def _surface_map(
    calibration: CameraCalibration, lens: tuple[float, ...] | None = None,
) -> _SurfaceMap:
    return _SurfaceMap(calibration, lens)


def photo_size_mm(
    calibration: CameraCalibration, lens: tuple[float, ...] | None = None,
) -> tuple[float, float]:
    """The widest area the photo fills, in whole mm (a crop side of 0).

    Centred on the calibrated area, so no part of it is outside the
    photo across the calibrated rows (width) and columns (height).
    """
    return _surface_map(calibration, lens).photo_size_mm()


def correct_image(
    jpeg: bytes,
    calibration: CameraCalibration,
    view: CameraView | None = None,
    lift: SurfaceLift | None = None,
    overlay: Overlay | None = None,
    lens: tuple[float, ...] | None = None,
) -> bytes:
    """Return the photo straightened onto the chosen area, as JPEG.

    Inside the calibrated area each grid cell is mapped exactly; outside
    it the nearest cell is extended, which is less accurate. With a
    ``lift`` the picture shows that surface to scale instead of the
    calibrated plane. An ``overlay`` is drawn on the result. A ``lens``
    guess continues the picture beyond the grid as the camera sees it
    (see ``_SurfaceMap``). Runs in an
    executor: decoding and mapping take tens of milliseconds.
    """
    from PIL import Image  # Home Assistant ships Pillow

    view = view or CameraView()
    if view.width_mm <= 0 or view.height_mm <= 0:
        photo_w, photo_h = photo_size_mm(calibration, lens)
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
    cell_mm_x, cell_mm_y = calibration.cell_mm
    # The view is centred on the straightened image's area
    area_h_mm = calibration.area_h_mm

    px_per_mm = min(
        PIXELS_PER_MM, MAX_OUTPUT_SIDE / max(view.width_mm, view.height_mm),
    )
    width = max(1, round(view.width_mm * px_per_mm))
    height = max(1, round(view.height_mm * px_per_mm))
    left_mm = calibration.area_mm / 2 + view.center_x_mm - view.width_mm / 2
    top_mm = area_h_mm / 2 + view.center_y_mm - view.height_mm / 2

    surface = _surface_map(calibration, lens)

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
    # Photo pixel of each mesh corner, each worked out once
    corners = [
        lift.seen_at(x_mm, y_mm) if lift is not None else (x_mm, y_mm)
        for y_mm, _ in ys
        for x_mm, _ in xs
    ]
    photo = [(x * scale_x, y * scale_y) for x, y in surface.photo_points(corners)]

    def src(col: int, row: int) -> tuple[float, float]:
        return photo[row * len(xs) + col]

    mesh = []
    for row, ((_, y0), (_, y1)) in enumerate(zip(ys, ys[1:])):
        if y1 <= y0:
            continue
        for col, ((_, x0), (_, x1)) in enumerate(zip(xs, xs[1:])):
            if x1 <= x0:
                continue
            # Pillow's quad order: upper left, lower left, lower
            # right, upper right.
            quad = (
                *src(col, row),
                *src(col, row + 1),
                *src(col + 1, row + 1),
                *src(col + 1, row),
            )
            mesh.append(((x0, y0, x1, y1), quad))
    result = image.transform(
        (width, height),
        Image.Transform.MESH,
        mesh,
        Image.Resampling.BILINEAR,
    )
    if overlay is not None:
        _draw_overlay(result, overlay, left_mm, top_mm, width / view.width_mm)
    out = io.BytesIO()
    result.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def calibration_overlay(
    jpeg: bytes, calibration: CameraCalibration, width: int = OUTPUT_WIDTH,
) -> bytes:
    """Return the raw photo with the calibration grid drawn on it, as JPEG.

    Shows which part of the bed the camera calibration covers, and how
    the lens bends its straight lines: each calibration point is a dot,
    joined to its neighbours along the grid rows and columns.
    """
    from PIL import Image, ImageDraw  # Home Assistant ships Pillow

    image = Image.open(io.BytesIO(jpeg))
    image.draft("RGB", (image.width // 2, image.height // 2))
    image = image.convert("RGB")
    sx = image.width / calibration.src_w
    sy = image.height / calibration.src_h
    points = [(x * sx, y * sy) for x, y in calibration.points]
    draw = ImageDraw.Draw(image)
    line = max(1, round(image.width / 800))
    color = (0, 255, 64)
    w, h = calibration.grid_w, calibration.grid_h
    for row in range(h):
        draw.line(points[row * w:(row + 1) * w], fill=color, width=line)
    for col in range(w):
        draw.line(points[col::w], fill=color, width=line)
    r = line * 2
    for x, y in points:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 32, 32))
    if image.width > width:
        image = image.resize(
            (width, round(image.height * width / image.width)),
            Image.Resampling.LANCZOS,
        )
    out = io.BytesIO()
    image.save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def rotate_image(jpeg: bytes, degrees: float, width: int = OUTPUT_WIDTH) -> bytes:
    """Return the photo turned ``degrees`` counter-clockwise, as JPEG.

    The result keeps the photo's shape and is cropped to the largest
    such rectangle inside the turned photo, so it has no empty corners.
    """
    import math

    from PIL import Image  # Home Assistant ships Pillow

    image = Image.open(io.BytesIO(jpeg))
    image.draft("RGB", (image.width // 2, image.height // 2))
    image = image.convert("RGB")
    w, h = image.size
    turned = image.rotate(degrees, Image.Resampling.BICUBIC, expand=True)
    c = abs(math.cos(math.radians(degrees)))
    s = abs(math.sin(math.radians(degrees)))
    k = min(w / (w * c + h * s), h / (w * s + h * c))
    cw, ch = w * k, h * k
    left = (turned.width - cw) / 2
    top = (turned.height - ch) / 2
    cropped = turned.crop((round(left), round(top), round(left + cw), round(top + ch)))
    if cropped.width > width:
        cropped = cropped.resize(
            (width, round(cropped.height * width / cropped.width)),
            Image.Resampling.LANCZOS,
        )
    out = io.BytesIO()
    cropped.save(out, "JPEG", quality=JPEG_QUALITY)
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
