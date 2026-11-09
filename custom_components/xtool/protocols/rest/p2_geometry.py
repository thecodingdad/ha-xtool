"""Where things are on the P2's bed, in the laser's coordinates.

Millimetres from home, X to the right and Y toward the front: the
coordinates Studio's canvas uses and the overview camera's straightened
picture shows (its calibrated area starts at the same point). Home is
20.5 mm in from the bed's left wall and 40.5 mm in from its back wall:
the corner of the Process on slats area.

Measured on a P2 (firmware 40.21.025): the overview picture's mat centre
line at X 327.4 (the bed's 0 reference for the slats, 348 mm from the
left wall), the close camera's picture at the head plus (54.9, -31.5),
and the close red dot at the head plus (64.4, -0.8). Studio uses (55,
-30) and the P2's own IR sensor offset (64.3, 0.9).
"""

from __future__ import annotations

from typing import Final

# The bed's left wall and back wall
BED_LEFT: Final = -20.5
BED_TOP: Final = -40.5

# Work areas: (x, y, width, height), from the P2 limits drawing
AREAS: Final[dict[str, tuple[float, float, float, float]]] = {
    "work_bed": (BED_LEFT, BED_TOP, 684.0, 376.0),
    "process_on_slats": (0.0, 0.0, 600.0, 305.0),
    "honeycomb_panel": (0.0, 0.0, 600.0, 305.0),
    "slats_removed": (50.0, 0.0, 500.0, 250.0),
}

# The close camera's picture: its corner relative to the head, and size
CLOSE_CAMERA_OFFSET: Final = (55.0, -30.0)
CLOSE_CAMERA_SIZE: Final = (140.0, 100.0)

# The IR distance sensor relative to the head, when the P2 does not say
# (its laserMeasureOffset setting)
MEASURE_OFFSET: Final = (64.3, 0.9)

# Slat positions: the 0 reference, the pitch, and how many each way.
# Numbered from the 0 reference outward: 1-24 to the left, 1-23 to the
# right (47 in all), each half a pitch further than the last number.
SLAT_ZERO_X: Final = 348.0 + BED_LEFT
SLAT_PITCH: Final = 13.2
SLATS_LEFT: Final = 24
SLATS_RIGHT: Final = 23

# The overview camera: the point below it and its height above the
# calibrated plane, fitted to the P2's calibration grid
OVERVIEW_CAMERA: Final = (307.4, -41.4, 174.0)

# The overview camera's lens, fitted to a P2's calibration grid (see
# camera_correction._fit_lens); each P2's own grid refines it
OVERVIEW_LENS: Final = (
    3.78293, -0.0036276, -0.00216825, -307.617, 72.4371, -162.671,
    -1826.99, -0.996363, 2227.47, 1744.78,
    -0.0391064, 0.0953019, -0.0592302, 0.0143858,
)

# Studio's distance (Honeycomb panel, Slats removed) is measured down
# from this height above the slats
DISTANCE_ZERO: Final = 25.0

# Processing modes and Studio's range for each one's setting: the
# material's thickness (Process on slats) or its distance (the others)
MODES: Final[dict[str, tuple[float, float]]] = {
    "process_on_slats": (0.0, 25.0),
    "honeycomb_panel": (0.0, 28.0),
    "slats_removed": (0.0, 75.0),
}
# Studio's setting until it is changed, in every mode
MODE_DEFAULT: Final = 3.0


def slats() -> list[tuple[float, str]]:
    """Each slat position's X and number."""
    left = [
        (SLAT_ZERO_X - (n - 0.5) * SLAT_PITCH, str(n)) for n in range(SLATS_LEFT, 0, -1)
    ]
    right = [
        (SLAT_ZERO_X + (n - 0.5) * SLAT_PITCH, str(n)) for n in range(1, SLATS_RIGHT + 1)
    ]
    return left + right


def close_camera_area(head_x: float, head_y: float) -> tuple[float, float, float, float]:
    """The close camera's picture for a head position."""
    return (
        head_x + CLOSE_CAMERA_OFFSET[0], head_y + CLOSE_CAMERA_OFFSET[1], *CLOSE_CAMERA_SIZE,
    )


def surface_height(mode: str, value: float) -> float:
    """The material's top above the slats, from a mode's setting."""
    if mode == "process_on_slats":
        return value
    return DISTANCE_ZERO - value


def mode_value(mode: str, height: float) -> float:
    """A mode's setting for a material whose top is this high above the slats."""
    return surface_height(mode, height)  # the same sum both ways
