"""P2 overlays a dashboard card can draw: each overlay switch's position and
style, and the Overlays select (Integration or Card) per camera."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from .common import COMPONENT
from .test_p2_views import _calibration, _view, coordinator  # noqa: F401 - fixture


def _correction(coordinator, camera="overview", view=None):  # noqa: F811
    from custom_components.xtool.protocols.rest.p2_entities import p2_correction
    from custom_components.xtool.protocols.ws_v2.camera_correction import CameraView

    return p2_correction(
        coordinator, camera, _calibration(), view or CameraView(610, 305, 0, 0),
    )


def _attributes(coordinator, key):  # noqa: F811
    from custom_components.xtool.protocols.rest.p2_entities import _overlay_attributes

    return _overlay_attributes(coordinator)[key]


def _sizes(view_width_mm=610.0):
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        MAX_OUTPUT_SIDE,
        PIXELS_PER_MM,
        overlay_sizes,
    )

    px_per_mm = min(PIXELS_PER_MM, MAX_OUTPUT_SIDE / view_width_mm)
    width = round(view_width_mm * px_per_mm)
    return overlay_sizes(width, width / view_width_mm), width / view_width_mm


def test_the_position_is_where_the_integration_draws(coordinator) -> None:  # noqa: F811
    view = _view(coordinator)
    coordinator.data.position_x, coordinator.data.position_y = 100.0, 50.0
    for key in (
        "overlay_laser_target", "overlay_slat_lines", "overlay_red_dot_close",
        "overlay_red_dot_quick", "overlay_mode_slats_removed",
    ):
        view[key] = True
    overlay = _correction(coordinator)["overlay"]
    target = _attributes(coordinator, "overlay_laser_target")["position"]
    assert target == {"target": [{"x": 100.0, "y": 50.0}]}
    assert [(p["x"], p["y"]) for p in target["target"]] == list(overlay.markers)
    assert _attributes(coordinator, "overlay_mode_slats_removed")["position"] == {
        "areas": [{"x": 50.0, "y": 0.0, "width": 500.0, "height": 250.0}],
    }
    assert [list(a[:4]) for a in overlay.areas] == [[50.0, 0.0, 500.0, 250.0]]
    slats = _attributes(coordinator, "overlay_slat_lines")["position"]["slats"]
    assert len(slats) == len(overlay.lines) == 47
    assert slats[1] == dict(zip(
        ("x0", "y0", "x1", "y1", "label"),
        [round(v, 1) if isinstance(v, float) else v for v in overlay.lines[1]],
    ))
    assert _attributes(coordinator, "overlay_red_dot_close")["position"] == {
        "close": [{"x": 164.8, "y": 50.7, "diameter": 0.9}],
    }
    (quick,) = _attributes(coordinator, "overlay_red_dot_quick")["position"]["quick"]
    assert quick == dict(zip(
        ("x", "y", "length", "width", "angle"), [round(v, 1) for v in overlay.spots[0]],
    ))


def test_switches_that_are_off_also_tell_where_their_overlay_is(coordinator) -> None:  # noqa: F811
    coordinator.data.position_x, coordinator.data.position_y = 10.0, 20.0
    assert _correction(coordinator)["overlay"] is None
    assert _attributes(coordinator, "overlay_laser_target")["position"] == {"target": [{"x": 10.0, "y": 20.0}]}
    assert _attributes(coordinator, "overlay_mode_process_on_slats")["position"]["areas"]
    from custom_components.xtool.protocols.rest.p2_entities import _overlay_attributes

    # The close camera's picture is a photo: no position or style
    assert "overlay_close_camera" not in _overlay_attributes(coordinator)


def test_the_style_is_how_the_integration_draws(coordinator) -> None:  # noqa: F811
    from custom_components.xtool.protocols.rest import p2_geometry as g

    _correction(coordinator)
    sizes, px_per_mm = _sizes()
    style = _attributes(coordinator, "overlay_laser_target")["style"]
    assert style == {
        "color": list(g.OVERLAY_COLORS["laser_target"]), "line": sizes.line,
        "style": "solid", "icon": "mdi:crosshairs",
        "size": round(4 * sizes.target / px_per_mm, 1),
    }
    area = _attributes(coordinator, "overlay_mode_honeycomb_panel")["style"]
    assert area["color"] == list(g.OVERLAY_COLORS["honeycomb_panel"])
    assert area["icon"] == "mdi:circle" and area["size"] == round(2 * sizes.dot / px_per_mm, 1)
    slats = _attributes(coordinator, "overlay_slat_lines")["style"]
    assert slats["line"] == sizes.thin and slats["icon"] is None and slats["size"] is None
    assert _attributes(coordinator, "overlay_red_dot_quick")["style"]["line"] == 3
    assert set(style) == {"color", "line", "style", "icon", "size"}


def test_the_line_follows_the_pictures_size(coordinator) -> None:  # noqa: F811
    from custom_components.xtool.protocols.ws_v2.camera_correction import CameraView

    _correction(coordinator, view=CameraView(610, 305, 0, 0))
    wide = _attributes(coordinator, "overlay_laser_target")["style"]
    _correction(coordinator, view=CameraView(100, 50, 0, 0))
    small = _attributes(coordinator, "overlay_laser_target")["style"]
    assert small["line"] == _sizes(100.0)[0].line
    assert small["line"] != wide["line"]


def test_the_close_cameras_position_is_from_its_area_centre(coordinator) -> None:  # noqa: F811
    from custom_components.xtool.protocols.ws_v2.entities import _thumbnail_registry

    close_camera = MagicMock()
    close_camera._calibration = _calibration()
    _thumbnail_registry(coordinator).cameras["closeup"] = close_camera
    view = _view(coordinator)
    coordinator.data.position_x, coordinator.data.position_y = 200.0, 100.0
    view["overlay_red_dot_close_closeup"] = True
    ring = _correction(coordinator, "closeup")["overlay"].rings[0]
    calibration = _calibration()
    # Drawn from the area's corner; the crop numbers count from its centre
    (close,) = _attributes(coordinator, "overlay_red_dot_close_closeup")["position"]["close"]
    assert close == {
        "x": round(ring[0] - calibration.area_mm / 2, 1),
        "y": round(ring[1] - calibration.area_h_mm / 2, 1),
        "diameter": 0.9,
    }
    studio = _attributes(coordinator, "overlay_studio_closeup")["position"]["areas"][0]
    # Studio's crop is centred: its centre is the crop numbers' 0, 0
    assert studio["x"] + studio["width"] / 2 == pytest.approx(0, abs=0.1)
    assert studio["y"] + studio["height"] / 2 == pytest.approx(0, abs=0.1)


def test_card_leaves_the_picture_to_the_card(coordinator) -> None:  # noqa: F811
    from custom_components.xtool.protocols.ws_v2.entities import _thumbnail_registry

    view = _view(coordinator)
    assert view["overlays"] == "integration" and view["overlays_closeup"] == "integration"
    view["overlay_laser_target"] = view["overlay_slat_lines"] = True
    view["overlays"] = "card"
    coordinator.data.position_x, coordinator.data.position_y = 30.0, 40.0
    assert _correction(coordinator)["overlay"] is None
    assert _attributes(coordinator, "overlay_laser_target")["position"] == {"target": [{"x": 30.0, "y": 40.0}]}
    # The close camera's picture is a photo: still laid on
    close = MagicMock()
    close._last_snapshot = b"jpeg"
    close._calibration = _calibration()
    _thumbnail_registry(coordinator).cameras["closeup"] = close
    view["overlay_close_camera"] = True
    overlay = _correction(coordinator)["overlay"]
    assert len(overlay.images) == 1
    assert overlay.markers == () and overlay.lines == () and overlay.areas == ()


def test_a_switch_is_written_only_when_its_overlay_changes(coordinator) -> None:  # noqa: F811
    from custom_components.xtool.protocols.rest.p2_entities import _overlay_switches

    switch = next(
        e for e in coordinator.build_switches() if e.translation_key == "overlay_laser_target"
    )
    switch.hass = MagicMock()
    switch.async_write_ha_state = MagicMock()
    _overlay_switches(coordinator)["overlay_laser_target"] = switch
    coordinator.data.position_x, coordinator.data.position_y = 1.0, 2.0
    _correction(coordinator)
    assert switch.async_write_ha_state.call_count == 1
    _correction(coordinator)
    assert switch.async_write_ha_state.call_count == 1
    coordinator.data.position_x = 5.0
    _correction(coordinator)
    assert switch.async_write_ha_state.call_count == 2
    assert switch.extra_state_attributes["position"] == {"target": [{"x": 5.0, "y": 2.0}]}


def test_position_and_style_are_not_recorded() -> None:
    from custom_components.xtool.protocols.rest.p2_entities import P2ViewSwitch

    assert {"position", "style"} <= P2ViewSwitch._unrecorded_attributes


def test_each_camera_has_an_overlays_select(coordinator) -> None:  # noqa: F811
    selects = {
        e.unique_id.split("camera_", 1)[1]: e
        for e in coordinator.build_selects() if "camera_" in e.unique_id
    }
    for key in ("overview_overlays", "closeup_overlays_closeup"):
        select = selects[key]
        assert select.options == ["integration", "card"]
        assert select.entity_category == "config"
        assert select.translation_key == "overlays"
        assert select.current_option == "integration"


def test_overlays_names() -> None:
    for name in ("translations/en.json",):
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))["entity"]
        assert data["select"]["overlays"] == {
            "name": "Overlays", "state": {"integration": "Integration", "card": "Card"},
        }
    icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))["entity"]
    assert "overlays" in icons["select"]


def _camera_view(coordinator, camera, view=None):  # noqa: F811
    from custom_components.xtool.protocols.rest.p2_entities import P2Camera
    from custom_components.xtool.protocols.ws_v2.entities import _thumbnail_registry

    entity = MagicMock(spec=P2Camera)
    entity._calibration = _calibration()
    _thumbnail_registry(coordinator).cameras[camera] = entity
    _correction(coordinator, camera, view)
    return entity.set_picture_view.call_args.args[0]


def test_the_camera_tells_the_area_its_picture_shows(coordinator) -> None:  # noqa: F811
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        CameraView,
        photo_size_mm,
    )

    assert _camera_view(coordinator, "overview") == {
        "x": 0.0, "y": 0.0, "width": 610.0, "height": 305.0,
    }
    # The overlays' frame: the laser's mm on the overview
    assert _camera_view(coordinator, "overview", CameraView(100, 50, -205, -102.5)) == {
        "x": 50.0, "y": 25.0, "width": 100.0, "height": 50.0,
    }
    # Uncropped: the whole photo
    width, height = photo_size_mm(_calibration(), None)
    _view(coordinator)["crop_preset_closeup"] = "uncropped"
    assert _camera_view(coordinator, "closeup") == {
        "x": round(-width / 2, 1), "y": round(-height / 2, 1),
        "width": round(width, 1), "height": round(height, 1),
    }
    # The close camera's: from its area's centre, as its overlays
    _view(coordinator)["crop_preset_closeup"] = "studio"
    assert _camera_view(coordinator, "closeup") == {
        "x": -70.0, "y": -50.0, "width": 140.0, "height": 100.0,
    }


def test_the_view_is_not_recorded() -> None:
    from custom_components.xtool.protocols.rest.p2_entities import P2Camera

    assert "view" in P2Camera._unrecorded_attributes


def test_red_dot_close_is_an_icon_like_the_laser_target(coordinator) -> None:  # noqa: F811
    from custom_components.xtool.protocols.rest import p2_geometry as g
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        Overlay,
        _draw_overlay,
    )

    _correction(coordinator)
    sizes, px_per_mm = _sizes()
    style = _attributes(coordinator, "overlay_red_dot_close")["style"]
    target = _attributes(coordinator, "overlay_laser_target")["style"]
    assert style == {
        "color": list(g.OVERLAY_COLORS["red_dots"]), "line": sizes.ring,
        "style": "solid", "icon": "mdi:circle-outline", "size": target["size"],
    }
    # Painted as that icon: a ring of radius 10/24 of the size, the dot visible inside
    from PIL import Image

    image = Image.new("RGB", (200, 200))
    _draw_overlay(image, Overlay(rings=((20.0, 20.0, 0.9),)), 0, 0, 5.0)
    small = overlay_sizes_of(200, 5.0)
    edge = round(100 + small.ring_radius - small.ring / 2)
    assert image.getpixel((edge, 100)) != (0, 0, 0)
    assert image.getpixel((100, 100)) == (0, 0, 0)


def overlay_sizes_of(width, px_per_mm):
    from custom_components.xtool.protocols.ws_v2.camera_correction import overlay_sizes

    return overlay_sizes(width, px_per_mm)
