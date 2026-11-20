"""P2 camera views: crop presets, overlays, slats and the material's height."""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import COMPONENT, has, requires

# The Mode areas' overlays are "overlay_mode_<mode>" since one Overlay close camera
V2 = has("p2_overlays_v2")
MODE = "mode_" if V2 else ""
from .test_camera_correction import GRID, _photo

pytestmark = requires("p2_views")

P2_GRID = {
    "grid_num_w": 62, "grid_num_h": 32, "image_src_w": 4656, "image_src_h": 3496,
    "image_dst_w": 2440, "image_dst_h": 1220, "thick0": 3.0,
    "data0": "".join(f"[{c * 70.0 + 100},{r * 100.0 + 100}]" for r in range(32) for c in range(62)),
}


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.const import XtoolStatus
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest import XTOOL_P2

    coord = build_coordinator(hass, XTOOL_P2)
    coord.config_entry = config_entry
    coord.data = XtoolDeviceState(status=XtoolStatus.IDLE)
    coord.data.available = True
    coord.protocol.measure_offset = MagicMock(return_value={"x": 64.3, "y": 0.9, "z": 136.7})
    return coord


def _calibration():
    from custom_components.xtool.protocols.ws_v2.camera_correction import parse_calibration

    return parse_calibration(P2_GRID, 4.0)


def _view(coordinator):
    from custom_components.xtool.protocols.rest.p2_entities import _p2_view

    return _p2_view(coordinator)


def _correction(coordinator, camera="overview"):
    from custom_components.xtool.protocols.rest.p2_entities import p2_correction
    from custom_components.xtool.protocols.ws_v2.camera_correction import CameraView

    return p2_correction(coordinator, camera, _calibration(), CameraView(610, 305, 0, 0))


def test_47_slats_numbered_from_the_0_reference() -> None:
    from custom_components.xtool.protocols.rest import p2_geometry as g

    slats = g.slats()
    assert len(slats) == 47
    assert [label for _, label in slats[:2]] == ["24", "23"]
    assert [label for _, label in slats[23:25]] == ["1", "1"]
    assert slats[-1][1] == "23"
    # 0 reference 348 mm from the left wall, half a pitch to each slat 1
    assert slats[23][0] == pytest.approx(327.5 - 6.6)
    assert slats[24][0] == pytest.approx(327.5 + 6.6)
    assert g.close_camera_area(200, 100) == (255.0, 70.0, 140.0, 100.0)


def test_plane_height_comes_from_the_calibration() -> None:
    assert _calibration().plane_mm == 3.0


def test_by_default_the_picture_is_unchanged(coordinator) -> None:
    correction = _correction(coordinator)
    assert correction["view"].width_mm == 610
    assert correction["overlay"] is None
    assert correction["lift"] is None  # Studio's default 3 mm is the calibrated plane


def test_a_crop_preset_shows_that_area(coordinator) -> None:
    _view(coordinator)["crop_preset"] = "slats_removed"
    view = _correction(coordinator)["view"]
    assert (view.width_mm, view.height_mm) == (500, 250)
    assert (view.center_x_mm, view.center_y_mm) == (300 - 305, 125 - 152.5)
    coordinator.data.position_x, coordinator.data.position_y = 200.0, 100.0
    _view(coordinator)["crop_preset"] = "close_camera_studio"
    view = _correction(coordinator)["view"]
    assert (view.width_mm, view.height_mm, view.center_x_mm, view.center_y_mm) == (
        140, 100, 325 - 305, 120 - 152.5,
    )


def test_one_setting_whose_meaning_follows_the_mode(coordinator) -> None:
    view = _view(coordinator)
    view["thickness_or_distance"] = 25.0
    assert _correction(coordinator)["lift"].surface_mm == 22.0  # a thickness
    view["processing_mode"] = "slats_removed"
    view["thickness_or_distance"] = 75.0
    assert _correction(coordinator)["lift"].surface_mm == 25 - 75 - 3  # a distance
    view["processing_mode"] = "honeycomb_panel"
    view["thickness_or_distance"] = 22.0
    assert _correction(coordinator)["lift"] is None
    view["thickness_or_distance"] = 75.0  # beyond the mode's range: its end
    assert _correction(coordinator)["lift"].surface_mm == 25 - 28 - 3


async def test_the_mode_sets_the_range_and_the_measured_meaning(coordinator) -> None:
    from custom_components.xtool.protocols.rest.p2_entities import P2Measured

    number = next(e for e in coordinator.build_numbers() if e.translation_key == "thickness_or_distance")
    assert (number.native_min_value, number.native_max_value) == (0.0, 25.0)
    select = next(e for e in coordinator.build_selects() if e.translation_key == "processing_mode")
    select.async_write_ha_state = MagicMock()
    coordinator.async_update_listeners = MagicMock()
    _view(coordinator)["thickness_or_distance"] = 60.0
    await select.async_select_option("honeycomb_panel")
    assert (number.native_max_value, number.native_value) == (28.0, 28.0)
    coordinator.async_update_listeners.assert_called()
    coordinator.data.measured_thickness_mm = 6.0
    measured = P2Measured(coordinator)
    assert measured.native_value == 19.0
    await select.async_select_option("process_on_slats")
    assert measured.native_value == 6.0


async def test_a_preset_fills_in_the_crop_and_editing_it_makes_it_custom(coordinator) -> None:
    from custom_components.xtool.protocols.ws_v2.entities import _camera_view, _crop_numbers

    _view(coordinator)["crop_preset"] = "slats_removed"
    _correction(coordinator)
    view = _camera_view(coordinator, "overview")
    # The overview's centre numbers are in the laser's mm
    assert (view.width_mm, view.height_mm, view.center_x_mm, view.center_y_mm) == (
        500, 250, 300, 125,
    )
    width = next(
        e for e in coordinator.build_numbers()
        if e.translation_key == "crop_width" and e._camera_name == "overview"
    )
    width.async_write_ha_state = MagicMock()
    coordinator.async_update_listeners = MagicMock()
    _crop_numbers(coordinator, "overview")["width"] = width
    await width.async_set_native_value(400)
    assert _view(coordinator)["crop_preset"] == "custom"
    coordinator.async_update_listeners.assert_called()
    view = _camera_view(coordinator, "overview")
    assert (view.width_mm, view.height_mm) == (400, 250)
    # A preset written in later shows in the number
    _view(coordinator)["crop_preset"] = "work_bed"
    _correction(coordinator)
    assert width.native_value == 684


def test_any_number_of_overlays(coordinator) -> None:
    from custom_components.xtool.protocols.rest import p2_geometry as g

    view = _view(coordinator)
    coordinator.data.position_x, coordinator.data.position_y = 100.0, 50.0
    assert _correction(coordinator)["overlay"] is None
    # The Work bed is a crop, not an overlay
    assert "overlay_work_bed" not in view
    view[f"overlay_{MODE}honeycomb_panel"] = view[f"overlay_{MODE}slats_removed"] = True
    overlay = _correction(coordinator)["overlay"]
    assert [a[:4] for a in overlay.areas] == [(0.0, 0.0, 600.0, 305.0), (50.0, 0.0, 500.0, 250.0)]
    assert overlay.areas[0][4] != overlay.areas[1][4]  # each area its own colour
    assert overlay.markers == () and overlay.lines == () and overlay.spots == ()
    assert overlay.rings == ()
    view["overlay_laser_target"] = view["overlay_slat_lines"] = True
    view["overlay_red_dot_close"] = view["overlay_red_dot_quick"] = True
    overlay = _correction(coordinator)["overlay"]
    assert overlay.markers == ((100.0, 50.0),)  # where the laser fires
    assert len(overlay.lines) == 47
    (quick,) = overlay.spots
    # The close dot: a ring it sits just inside
    assert overlay.rings == (pytest.approx((164.8, 50.7, 0.9)),)
    assert quick[:2] == (343.2, 81.9) and quick[2] > quick[3]  # long toward its source
    assert 90 < quick[4] < 180  # from the back right: toward the left and front
    assert g.quick_red_dot_angle() == quick[4]
    # Every slat's line, the odd numbers written
    labels = [line[4] for line in overlay.lines]
    assert labels[:3] == ["", "23", ""] and labels[23:25] == ["1", "1"]
    assert all(label == "" or int(label) % 2 for label in labels)
    if not V2:
        view["overlay_close_camera_studio"] = True
        assert overlay_rects(_correction(coordinator))[-1] == (155.0, 20.0, 140.0, 100.0)


def overlay_rects(correction):
    return [a[:4] for a in correction["overlay"].areas]


def test_close_camera_overlays_move_with_the_head(coordinator) -> None:
    from custom_components.xtool.protocols.rest.p2_entities import p2_view_needs_head

    view = _view(coordinator)
    assert _correction(coordinator, "closeup")["overlay"] is None
    assert not p2_view_needs_head(coordinator, "closeup")
    coordinator.data.position_x, coordinator.data.position_y = 200.0, 100.0
    # The close camera's area starts at the head plus (55, -30): (255, 70)
    # Where the laser fires is never in the close camera's picture: no overlay
    assert "overlay_laser_target_closeup" not in view
    view["overlay_red_dot_close_closeup"] = True
    ring = _correction(coordinator, "closeup")["overlay"].rings[0]
    assert ring == pytest.approx((9.8, 30.7, 0.9))
    view["overlay_slat_lines_closeup"] = True
    lines = _correction(coordinator, "closeup")["overlay"].lines
    assert len(lines) == 47 and lines[23][0] == pytest.approx(327.5 - 6.6 - 255)
    view["overlay_studio_closeup"] = True
    if V2:
        # The overview's areas are no longer drawn on the close camera
        assert "overlay_overview_process_on_slats_closeup" not in view
        assert overlay_rects(_correction(coordinator, "closeup")) == [(0.0, 0.0, 140.0, 100.0)]
    else:
        view["overlay_overview_process_on_slats_closeup"] = True
        assert overlay_rects(_correction(coordinator, "closeup")) == [
            (0.0, 0.0, 140.0, 100.0), (-255.0, -70.0, 600.0, 305.0),
        ]
    assert p2_view_needs_head(coordinator, "closeup")


def test_overview_crop_centre_is_in_laser_coordinates(coordinator) -> None:
    from custom_components.xtool.protocols.ws_v2.entities import _crop_settings

    rows = {key: spec for key, *spec in _crop_settings(coordinator, "overview")}
    assert rows["center_x"][:3] == [305.0, 0, 610.0]
    assert rows["center_y"][:3] == [152.5, 0, 305.0]
    assert rows["width"][:3] == [610.0, 0, 915]
    close = {key: spec for key, *spec in _crop_settings(coordinator, "closeup")}
    assert close["center_x"][:3] == [0, -70, 70]


def test_close_camera_crop_presets(coordinator) -> None:
    from custom_components.xtool.protocols.ws_v2.entities import _camera_view

    view = _view(coordinator)
    view["crop_preset_closeup"] = "uncropped"
    assert _correction(coordinator, "closeup")["view"] == CameraViewOf(0, 0, 0, 0)
    assert _camera_view(coordinator, "closeup") == CameraViewOf(0, 0, 0, 0)
    view["crop_preset_closeup"] = "studio"
    assert _correction(coordinator, "closeup")["view"] == CameraViewOf(140, 100, 0, 0)
    view["crop_preset"] = "uncropped"
    assert _correction(coordinator)["view"] == CameraViewOf(0, 0, 0, 0)


def CameraViewOf(*values):
    from custom_components.xtool.protocols.ws_v2.camera_correction import CameraView

    return CameraView(*values)


def test_correct_image_draws_and_lifts() -> None:
    from PIL import Image

    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        CameraView, Overlay, SurfaceLift, correct_image, parse_calibration,
    )

    calibration = parse_calibration(GRID)
    plain = correct_image(_photo(), calibration, CameraView(200, 200, 0, 0))
    drawn = correct_image(
        _photo(), calibration, CameraView(200, 200, 0, 0),
        overlay=Overlay(areas=((50, 50, 100, 100, (255, 214, 0)),), lines=((10, 0, 10, 200, "1"),),
                        markers=((100, 100),), spots=((60, 60, 5, 2, 150),), rings=((80, 80, 2),)),
    )
    lifted = correct_image(
        _photo(), calibration, CameraView(200, 200, 0, 0), lift=SurfaceLift(100, 100, 200, 50),
    )
    assert Image.open(io.BytesIO(drawn)).size == Image.open(io.BytesIO(plain)).size
    assert drawn != plain and lifted != plain
    # A raised surface seen from above looks larger: away from the point below
    lift = SurfaceLift(100, 100, 200, 50)
    assert lift.seen_at(150, 100) == pytest.approx((100 + 50 * 200 / 150, 100))


def test_view_entities_are_on_the_camera_devices(coordinator) -> None:
    switches = {e.translation_key for e in coordinator.build_switches()}
    numbers = {e.translation_key for e in coordinator.build_numbers()}
    keys = {e.translation_key for e in coordinator.build_selects()}
    assert {"crop_preset", "processing_mode"} <= keys
    assert {"overlay_laser_target", "overlay_red_dot_quick", "overlay_close_camera"} <= switches
    assert "overlay_work_bed" not in switches
    targets = [e for e in coordinator.build_switches() if e.translation_key == "overlay_laser_target"]
    assert [e.unique_id.split("camera_", 1)[1] for e in targets] == ["overview_overlay_laser_target"]
    assert not {"slat_overlay", "overlay", "laser_target", "slat_lines"} & switches
    assert "overlay" not in keys
    assert "thickness_or_distance" in numbers
    assert not {"thickness_on_slats", "distance_honeycomb_panel", "distance_slats_removed"} & numbers
    crop = next(e for e in coordinator.build_selects() if e.translation_key == "crop_preset")
    assert crop.unique_id.endswith("camera_overview_crop_preset")
    assert crop.device_info != coordinator.build_selects()[0].device_info
    selects = {e.unique_id.split("camera_", 1)[1]: e for e in coordinator.build_selects()
               if "camera_" in e.unique_id}
    assert selects["closeup_crop_preset_closeup"].options == ["uncropped", "studio", "custom"]
    assert selects["overview_crop_preset"].options[0] == "uncropped"
    # Controls: Crop, its numbers, Mode, and Thickness or distance
    assert crop.entity_category is None
    assert selects["overview_processing_mode"].entity_category is None
    overlay = next(e for e in coordinator.build_switches() if e.translation_key == "overlay_slat_lines")
    assert overlay.entity_category == "config"
    for number in coordinator.build_numbers():
        if number.translation_key in ("thickness_or_distance", "crop_width", "crop_center_y"):
            assert number.entity_category is None, number.translation_key


async def test_a_setting_change_refreshes_the_picture(coordinator) -> None:
    from custom_components.xtool.protocols.ws_v2.entities import _thumbnail_registry

    camera = MagicMock()
    camera.refresh_picture = AsyncMock()
    _thumbnail_registry(coordinator).cameras["overview"] = camera
    key = f"overlay_{MODE}slats_removed"
    switch = next(e for e in coordinator.build_switches() if e.translation_key == key)
    switch.async_write_ha_state = MagicMock()
    await switch.async_turn_on()
    assert _view(coordinator)[key] is True
    camera.refresh_picture.assert_awaited_once()


def test_names_and_states() -> None:
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))["entity"]
        assert data["select"]["crop_preset"]["name"] == "Crop"
        assert data["select"]["processing_mode"]["name"] == "Mode"
        assert data["select"]["crop_preset"]["state"]["work_bed"] == "Work bed"
        assert data["select"]["crop_preset"]["state"]["close_camera_studio"] == "Close camera (studio)"
        assert data["select"]["crop_preset"]["state"]["uncropped"] == "Uncropped"
        assert "overlay" not in data["select"]
        assert data["switch"]["overlay_laser_target"]["name"] == "Overlay laser target"
        if V2:
            assert data["switch"]["overlay_mode_slats_removed"]["name"] == "Overlay mode slats removed"
            assert data["switch"]["overlay_close_camera"]["name"] == "Overlay close camera"
            assert not [k for k in data["switch"] if k.startswith("overlay_overview_")]
            assert data["button"]["wake"]["name"] == "Wake"
        else:
            assert data["switch"]["overlay_overview_work_bed"]["name"] == "Overlay overview (work bed)"
        assert data["switch"]["overlay_red_dot_quick"]["name"] in (
            "Overlay red dot (quick)", "Overlay red dot quick",
        )
        assert data["button"]["measure_thickness_or_distance"]["name"] == (
            "Measure thickness or distance"
        )
        assert data["select"]["processing_mode"]["state"]["slats_removed"] == "Slats removed"
        assert data["switch"]["overlay_slat_lines"]["name"] == "Overlay slat lines"
        assert data["number"]["thickness_or_distance"]["name"] == "Thickness or distance"


async def test_while_asleep_the_last_photo_shows_new_settings(coordinator) -> None:
    from custom_components.xtool.const import XtoolStatus

    camera = next(c for c in coordinator.build_cameras() if c._camera_name == "overview")
    camera.hass = coordinator.hass
    camera._calibration = _calibration()
    camera._last_photo = _photo()
    camera._last_snapshot = b"old"
    coordinator.data.status = XtoolStatus.SLEEPING
    coordinator.protocol.camera_snap = AsyncMock(side_effect=AssertionError("a photo wakes the P2"))
    first = await camera.async_camera_image()
    assert first and first != b"old"
    # Without a change the picture is not drawn again
    assert await camera.async_camera_image() is first
    _view(coordinator)["overlay_slat_lines"] = True
    camera._redraw = True  # what a setting's refresh_picture does
    assert await camera.async_camera_image() != first


def test_red_dot_and_head_icons() -> None:
    icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))["entity"]["switch"]
    assert icons["quick_measure_light"] == {"default": "mdi:laser-pointer"}
    # Red dot (close): the dot below the head
    assert icons["aimed_measure_light"] == {"default": "mdi:pan-down" if V2 else "mdi:target"}


def test_beyond_the_grid_the_lens_continues_the_picture() -> None:
    """A grid made by a known lens: beyond it, points follow that lens."""
    import numpy as np

    from custom_components.xtool.protocols.rest import p2_geometry as g
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        _lens_project, _surface_map, parse_calibration,
    )

    mm = np.array([(c * 10.0, r * 10.0) for r in range(32) for c in range(62)])
    true_lens = np.array(g.OVERVIEW_LENS) * (1 + 0.002 * np.arange(14) / 14)
    pixels = _lens_project(true_lens, mm)
    raw = {"grid_num_w": 62, "grid_num_h": 32, "image_src_w": 4656, "image_src_h": 3496,
           "image_dst_w": 2440, "image_dst_h": 1220, "thick0": 3.0,
           "data0": "".join(f"[{x:.4f},{y:.4f}]" for x, y in pixels)}
    calibration = parse_calibration(raw, 4.0)
    surface = _surface_map(calibration, g.OVERVIEW_LENS)
    assert surface._lens is not None
    for point in ((-20.5, -40.5), (663.5, 335.5), (300.0, -30.0), (650.0, 150.0)):
        expected = _lens_project(true_lens, np.array([point]))[0]
        assert surface.photo_point(*point) == pytest.approx(tuple(expected), abs=1.0)
        assert surface.photo_points([point])[0] == pytest.approx(tuple(expected), abs=1.0)
    # Without a lens guess the cubic fit is used, as for the F2 Ultra UV
    assert _surface_map(calibration)._lens is None


def test_sync_to_mode_shows_the_modes_area(coordinator) -> None:
    from custom_components.xtool.protocols.rest.p2_entities import P2_CROP_OPTIONS

    assert P2_CROP_OPTIONS["overview"][:2] == ["uncropped", "sync_to_mode"]
    assert "sync_to_mode" not in P2_CROP_OPTIONS["closeup"]
    view = _view(coordinator)
    view["crop_preset"] = "sync_to_mode"
    assert _correction(coordinator)["view"] == CameraViewOf(600, 305, 300 - 305, 0)
    view["processing_mode"] = "slats_removed"
    assert _correction(coordinator)["view"] == CameraViewOf(500, 250, 300 - 305, 125 - 152.5)


def test_the_close_cameras_picture_on_the_overview(coordinator) -> None:
    from custom_components.xtool.protocols.ws_v2.entities import _thumbnail_registry

    close = MagicMock()
    close._last_snapshot = b"close-jpeg"
    close._calibration = None
    _thumbnail_registry(coordinator).cameras["closeup"] = close
    coordinator.data.position_x, coordinator.data.position_y = 200.0, 100.0
    _view(coordinator)["overlay_close_camera" if V2 else "overlay_close_camera_image"] = True
    overlay = _correction(coordinator)["overlay"]
    # At the close camera's crop (Custom: as Studio shows it), where it is
    assert overlay.images == ((255.0, 70.0, 140.0, 100.0, b"close-jpeg"),)


def test_overlay_colours_come_from_the_options(coordinator, hass) -> None:
    from custom_components.xtool.const import CONF_OVERLAY_COLORS

    hass.config_entries.async_update_entry(
        coordinator.config_entry,
        options={CONF_OVERLAY_COLORS: {"slats_removed": [1, 2, 3], "slat_lines": [4, 5, 6]}},
    )
    view = _view(coordinator)
    view[f"overlay_{MODE}slats_removed"] = view["overlay_slat_lines"] = True
    view[f"overlay_{MODE}process_on_slats"] = True
    overlay = _correction(coordinator)["overlay"]
    assert overlay.areas[1][4] == (1, 2, 3)
    assert overlay.areas[0][4] == (60, 220, 90)  # not chosen: the default
    assert overlay.line_color == (4, 5, 6)


def test_the_options_offer_a_colour_picker_per_overlay() -> None:
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        section = data["options"]["step"]["init"]["sections"]["overlay_colors"]
        assert section["name"] == "Overlay colors"
        assert set(section["data"]) == {
            "process_on_slats", "honeycomb_panel", "slats_removed",
            "studio", "custom", "laser_target", "slat_lines", "red_dots",
        }
        assert data["entity"]["select"]["crop_preset"]["state"]["sync_to_mode"] == "Sync to Mode"
        if V2:
            assert data["entity"]["switch"]["overlay_close_camera"]["name"] == "Overlay close camera"
        else:
            assert data["entity"]["switch"]["overlay_close_camera_image"]["name"] == (
                "Overlay close camera (image)"
            )


async def test_a_colour_change_redraws_without_a_reload(coordinator, monkeypatch) -> None:
    from custom_components.xtool import _async_options_updated
    from custom_components.xtool.const import CONF_OVERLAY_COLORS
    from custom_components.xtool.protocols.ws_v2.entities import _thumbnail_registry

    reload = AsyncMock()
    monkeypatch.setattr(coordinator.hass.config_entries, "async_reload", reload)
    entry = coordinator.config_entry
    entry.runtime_data = coordinator
    coordinator.options_at_setup = dict(entry.options)
    camera = MagicMock()
    camera.refresh_picture = AsyncMock()
    _thumbnail_registry(coordinator).cameras["overview"] = camera
    coordinator.hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_OVERLAY_COLORS: {"slat_lines": [1, 2, 3]}},
    )
    await _async_options_updated(coordinator.hass, entry)
    reload.assert_not_awaited()
    camera.refresh_picture.assert_awaited_once()
    coordinator.hass.config_entries.async_update_entry(entry, options={**entry.options, "x": 1})
    await _async_options_updated(coordinator.hass, entry)
    reload.assert_awaited_once()


def test_the_whole_photo_follows_the_lens() -> None:
    """A side of 0 is as far as the photo reaches, worked out once."""
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        _surface_map, photo_size_mm,
    )

    calibration = _calibration()
    first = photo_size_mm(calibration)
    assert first == photo_size_mm(calibration)
    assert _surface_map(calibration, None)._photo_size == first


def test_the_quick_red_dot_is_an_outline() -> None:
    """Like the close dot's ring: the spot's inside is left as the camera sees it."""
    from PIL import Image

    from custom_components.xtool.protocols.ws_v2.camera_correction import Overlay, _draw_overlay

    image = Image.new("RGB", (200, 200), (0, 0, 0))
    _draw_overlay(image, Overlay(spots=((50, 50, 20, 8, 0),), spot_color=(255, 0, 0)), 0, 0, 2.0)
    assert image.getpixel((100, 100)) == (0, 0, 0)  # the middle is not painted
    assert image.getpixel((100 + 21, 100))[0] > 200  # 3 px outside the 40 px length
    assert image.getpixel((100, 100 + 9))[0] > 200  # and outside the 16 px width
    assert image.getpixel((100 + 30, 100)) == (0, 0, 0)


def _lens_grid(continued: bool):
    """A 62 x 32 grid made by a known lens; its outer points optionally continued on straight lines."""
    import numpy as np

    from custom_components.xtool.protocols.rest import p2_geometry as g
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        _lens_project, parse_calibration,
    )

    mm = np.array([(c * 10.0, r * 10.0) for r in range(32) for c in range(62)])
    true_lens = np.array(g.OVERVIEW_LENS) * (1 + 0.002 * np.arange(14) / 14)
    pixels = _lens_project(true_lens, mm).reshape(32, 62, 2)
    if continued:
        # As the P2's file: 6 columns each side and 2 rows at the front, evenly spaced on a line
        for c in range(5, -1, -1):
            pixels[:, c] = 2 * pixels[:, c + 1] - pixels[:, c + 2]
        for c in range(56, 62):
            pixels[:, c] = 2 * pixels[:, c - 1] - pixels[:, c - 2]
        for r in range(30, 32):
            pixels[r] = 2 * pixels[r - 1] - pixels[r - 2]
    raw = {"grid_num_w": 62, "grid_num_h": 32, "image_src_w": 4656, "image_src_h": 3496,
           "image_dst_w": 2440, "image_dst_h": 1220, "thick0": 3.0,
           "data0": "".join(f"[{x:.4f},{y:.4f}]" for x, y in pixels.reshape(-1, 2))}
    return parse_calibration(raw, 4.0), true_lens


@requires("measured_bounds")
def test_continued_grid_points_are_found_and_replaced_by_the_lens() -> None:
    """The P2's grid is measured only in its middle; the lens takes over outside that."""
    import numpy as np

    from custom_components.xtool.protocols.rest import p2_geometry as g
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        _lens_project, _measured_bounds, _SurfaceMap,
    )

    calibration, true_lens = _lens_grid(continued=True)
    assert _measured_bounds(calibration) == (7, 54, 0, 28)
    surface = _SurfaceMap(calibration, g.OVERVIEW_LENS)
    assert surface._bounds == (70.0, 540.0, 0.0, 280.0)
    # Inside the grid's continued part and beyond the grid: as the lens sees
    for point in ((0.0, 150.0), (30.0, 20.0), (600.0, 150.0), (640.0, 100.0), (300.0, 310.0), (-15.0, 250.0)):
        expected = _lens_project(true_lens, np.array([point]))[0]
        assert surface.photo_point(*point) == pytest.approx(tuple(expected), abs=1.5)
        assert surface.photo_points([point])[0] == pytest.approx(tuple(expected), abs=1.5)
    # The grid itself says otherwise there (it was continued straight)
    straight = surface._grid(600.0, 150.0)
    lens = _lens_project(true_lens, np.array([[600.0, 150.0]]))[0]
    assert abs(straight[0] - lens[0]) > 5
    # In the measured part the grid is used as it is
    assert surface.photo_point(300.0, 150.0) == pytest.approx(surface._grid(300.0, 150.0))


@requires("measured_bounds")
def test_a_fully_measured_grid_is_used_whole() -> None:
    from custom_components.xtool.protocols.rest import p2_geometry as g
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        _measured_bounds, _SurfaceMap,
    )

    calibration, _ = _lens_grid(continued=False)
    assert _measured_bounds(calibration) == (0, 61, 0, 31)
    assert _SurfaceMap(calibration, g.OVERVIEW_LENS)._bounds == (0.0, 610.0, 0.0, 310.0)
    # Without a lens the whole grid is used, whatever its points (the F2 Ultra UV's way)
    continued, _ = _lens_grid(continued=True)
    assert _SurfaceMap(continued)._bounds == (0.0, 610.0, 0.0, 310.0)
