"""Tests for camera devices, the main camera crop and the xtool.camera action (issue #4).

Each camera of a laser with several cameras is its own device, connected
to the laser, with its settings. Names and entity IDs stay as they were
on the laser's device. A crop width or height of 0 shows the whole photo.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import SERIAL, build_coordinator
from .features import has, requires
from .test_camera_correction import GRID, _photo

pytestmark = requires("camera_devices")

ROOT = Path("custom_components/xtool")
SETTINGS = ("width", "height", "center_x", "center_y")


def _model(model_id: str = "F2UltraUV"):
    from custom_components.xtool.protocols import DEVICE_MODELS

    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == model_id and m.protocol_version == "V2"
    )


def _f2uv(hass: HomeAssistant):
    coordinator = build_coordinator(hass, _model())
    coordinator.protocol.camera_snap = AsyncMock(return_value=_photo())
    coordinator.protocol.camera_calibration_file = AsyncMock(
        return_value=json.dumps(GRID).encode(),
    )
    return coordinator


def _number(coordinator, key: str):
    unique_id = f"{SERIAL}_{key}"
    return next(n for n in coordinator.build_numbers() if n.unique_id == unique_id)


def _camera(coordinator, name: str):
    return next(c for c in coordinator.build_cameras() if c._camera_name == name)


def _identifiers(entity) -> set:
    return entity.device_info["identifiers"]


def test_each_camera_and_its_settings_are_on_its_own_device(hass: HomeAssistant) -> None:
    coordinator = _f2uv(hass)
    main = {("xtool", f"{SERIAL}:camera_main")}
    deep = {("xtool", f"{SERIAL}:camera_deep")}
    assert _identifiers(_camera(coordinator, "main")) == main
    assert _identifiers(_number(coordinator, "camera_main_thumbnail_refresh")) == main
    for setting in SETTINGS:
        assert _identifiers(_number(coordinator, f"camera_main_view_{setting}")) == main
    assert _identifiers(_camera(coordinator, "deep")) == deep
    assert _identifiers(_number(coordinator, "camera_deep_thumbnail_refresh")) == deep
    # Everything else stays on the laser.
    others = [
        n for n in coordinator.build_numbers()
        if "camera" not in n.unique_id
    ]
    assert others
    for number in others:
        assert _identifiers(number) == {("xtool", SERIAL)}, number.unique_id


def test_camera_devices_are_connected_to_the_laser(hass: HomeAssistant) -> None:
    coordinator = _f2uv(hass)
    coordinator.laser_device_id = "laser-device-id"
    info = _camera(coordinator, "main").device_info
    assert info.get("via_device_id") == "laser-device-id" or info.get("via_device") == ("xtool", SERIAL)
    assert info["manufacturer"] == "xTool"


def test_names_keep_the_entity_ids(hass: HomeAssistant) -> None:
    """Device "<laser> Main camera" + no entity name gives camera.<laser>_main_camera."""
    coordinator = _f2uv(hass)
    camera = _camera(coordinator, "main")
    info = camera.device_info
    assert info["name"] == "xTool Test Main camera"
    assert info["translation_key"] == "camera_main"
    assert info["translation_placeholders"] == {"laser": "xTool Test"}
    assert camera.name is None
    assert _camera(coordinator, "deep").device_info["name"] == "xTool Test Deep camera"
    keys = {
        _number(coordinator, f"camera_main_view_{s}").translation_key for s in SETTINGS
    }
    assert keys == {f"crop_{s}" for s in SETTINGS}
    assert _number(coordinator, "camera_main_thumbnail_refresh").translation_key == "thumbnail_refresh"


def test_camera_devices_follow_a_renamed_laser(hass: HomeAssistant, config_entry) -> None:
    """The user renamed "F2 Ultra UV" to "xTool F2 Ultra UV": the cameras follow, so they sort under it."""
    from homeassistant.helpers import device_registry as dr

    coordinator = _f2uv(hass)
    registry = dr.async_get(hass)
    laser = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("xtool", SERIAL)},
        name="xTool Test",
    )
    registry.async_update_device(laser.id, name_by_user="My laser")
    coordinator.laser_device_id = laser.id
    info = _camera(coordinator, "main").device_info
    assert info["name"] == "My laser Main camera"
    assert info["translation_placeholders"] == {"laser": "My laser"}


def test_fire_record_stays_on_the_laser(hass: HomeAssistant) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    for model in DEVICE_MODELS.values():
        if model.protocol_version != "V2" or not getattr(model, "has_fire_record", False):
            continue
        coordinator = build_coordinator(hass, model)
        fire = next(c for c in coordinator.build_cameras() if c._camera_name == "fireRecord")
        assert _identifiers(fire) == {("xtool", SERIAL)}


def test_a_single_camera_stays_on_the_laser(hass: HomeAssistant) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    singles = [
        m for m in DEVICE_MODELS.values()
        if m.protocol_version == "V2" and m.has_camera and len(m.camera_names) == 1
    ]
    assert singles
    for model in singles:
        coordinator = build_coordinator(hass, model)
        camera = _camera(coordinator, model.camera_names[0])
        assert _identifiers(camera) == {("xtool", SERIAL)}


@pytest.mark.parametrize(
    "path", [ROOT / "strings.json", ROOT / "translations" / "en.json", ROOT / "translations" / "de.json"],
)
def test_translations_name_the_devices_and_settings(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for camera in ("main", "deep", "overview", "closeup", "far", "near", "side"):
        assert "{laser}" in data["device"][f"camera_{camera}"]["name"]
    numbers = data["entity"]["number"]
    for key in ("thumbnail_refresh", *(f"crop_{s}" for s in SETTINGS)):
        assert key in numbers, key
    assert "camera" in data["services"]
    assert {"camera_wrong_entity", "camera_needs_a_value"} <= set(data["exceptions"])


def test_english_names() -> None:
    numbers = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))["entity"]["number"]
    assert numbers["crop_width"]["name"] == "Crop width"
    assert numbers["crop_height"]["name"] == "Crop height"
    assert numbers["crop_center_x"]["name"] == "Crop center X"
    assert numbers["crop_center_y"]["name"] == "Crop center Y"


def test_crop_numbers(hass: HomeAssistant) -> None:
    coordinator = _f2uv(hass)
    width = _number(coordinator, "camera_main_view_width")
    height = _number(coordinator, "camera_main_view_height")
    center_x = _number(coordinator, "camera_main_view_center_x")
    center_y = _number(coordinator, "camera_main_view_center_y")
    if has("crop_center_icons"):
        assert center_x.icon == "mdi:format-horizontal-align-center"
        assert center_y.icon == "mdi:format-vertical-align-center"
    else:
        assert center_x.icon == "mdi:arrow-left-right"
        assert center_y.icon == "mdi:arrow-up-down"
    for number in (width, height, center_x, center_y):
        # Not a distance: the xtool.focus picker lists only Focus height.
        assert number.device_class is None
        assert number.native_unit_of_measurement == "mm"
    assert (width.native_min_value, width.native_max_value) == (0, 460)
    assert (height.native_min_value, height.native_max_value) == (0, 460)
    assert (width.native_value, height.native_value) == (200.0, 200.0)


@pytest.mark.parametrize(("value", "stored"), [(0, 0.0), (5, 20.0), (120, 120.0), (600, 460.0)])
def test_crop_side_values(hass: HomeAssistant, value: float, stored: float) -> None:
    from custom_components.xtool.protocols.ws_v2.entities import _camera_view_values

    coordinator = _f2uv(hass)
    width = _number(coordinator, "camera_main_view_width")
    width._apply(value)
    assert width.native_value == stored
    assert _camera_view_values(coordinator)["width"] == stored


def test_photo_size_from_the_calibration() -> None:
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        parse_calibration,
        photo_size_mm,
    )

    # The grid covers half the photo each way, one cell short of each
    # edge: 2 + 2 cells of 100 mm.
    assert photo_size_mm(parse_calibration(json.dumps(GRID))) == (400.0, 400.0)


def test_photo_size_with_a_turned_grid() -> None:
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        parse_calibration,
        photo_size_mm,
    )

    turned = {
        **GRID,
        "data0": "[300,225][200,225][100,225][300,150][200,150][100,150][300,75][200,75][100,75]",
    }
    assert photo_size_mm(parse_calibration(json.dumps(turned))) == (400.0, 400.0)


def _lens(u: float, v: float) -> tuple[float, float]:
    """A smooth lens distortion: work surface (mm) to photo pixel."""
    s = (u - 100) / 100
    t = (v - 100) / 100
    return (2000 + 900 * s + 40 * s ** 3 + 25 * s * t * t, 1500 + 900 * t + 30 * t ** 3 + 20 * s * s * t)


def test_beyond_the_grid_lines_stay_smooth() -> None:
    """Outside the calibrated area the map follows the lens smoothly, not the outer cells."""
    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        _surface_map,
        parse_calibration,
    )

    points = "".join(
        "[{},{}]".format(*_lens(col * 10, row * 10)) for row in range(21) for col in range(21)
    )
    calibration = parse_calibration(json.dumps({
        "grid_num_w": 21, "grid_num_h": 21, "image_src_w": 4000, "image_src_h": 3000,
        "image_dst_w": 2000, "image_dst_h": 2000, "data0": points,
    }))
    surface = _surface_map(calibration)
    for u, v in ((-60, 37), (-30, 150), (260, 5), (100, -40), (250, 250)):
        x, y = surface.photo_point(u, v)
        want_x, want_y = _lens(u, v)
        assert abs(x - want_x) < 0.5 and abs(y - want_y) < 0.5, (u, v)
    # Inside, the grid is exact.
    assert surface.photo_point(50, 70) == pytest.approx(_lens(50, 70), abs=0.5)


def test_zero_shows_the_whole_photo() -> None:
    from PIL import Image

    from custom_components.xtool.protocols.ws_v2.camera_correction import (
        CameraView,
        correct_image,
        parse_calibration,
    )

    calibration = parse_calibration(json.dumps(GRID))
    whole = Image.open(io.BytesIO(correct_image(_photo(), calibration, CameraView(0, 200, 0, 0))))
    # 400 x 200 mm, at the largest size a picture may have (1600 pixels).
    assert whole.size == (1600, 800)
    # The left and right edges come from the photo outside the grid (red).
    for x in (10, 1589):
        pixel = whole.getpixel((x, 400))
        assert pixel[0] > 200 and pixel[1] < 60


@requires("camera_limits_per_camera")
def test_action_schema() -> None:
    from custom_components.xtool.services import CAMERA_SCHEMA

    schema = vol.Schema(CAMERA_SCHEMA)
    assert schema({"crop_width": 0, "crop_center_y": -12}) == {"crop_width": 0.0, "crop_center_y": -12.0}
    # The schema only converts: each camera's own limits are checked by the action
    assert schema({"crop_width": "684"}) == {"crop_width": 684.0}
    with pytest.raises(vol.Invalid):
        schema({"crop_width": "wide"})


@requires("camera_limits_per_camera")
async def test_action_refuses_values_outside_the_f2uv_limits(hass: HomeAssistant) -> None:
    from custom_components.xtool.services import _async_camera

    coordinator = _f2uv(hass)
    camera = _camera(coordinator, "main")
    camera.refresh_picture = AsyncMock()
    for key, value in (("crop_width", 461), ("crop_center_x", 201), ("crop_center_y", -201)):
        call = MagicMock()
        call.data = {key: float(value)}
        with pytest.raises(ServiceValidationError) as error:
            await _async_camera(camera, call)
        assert error.value.translation_key == "camera_value_out_of_range"
        assert error.value.translation_placeholders["field"] == key
        assert error.value.translation_placeholders["value"] == str(value)
    camera.refresh_picture.assert_not_awaited()


async def test_action_is_registered(hass: HomeAssistant) -> None:
    from custom_components.xtool.services import async_setup_services

    async_setup_services(hass)
    assert hass.services.has_service("xtool", "camera")
    assert hass.services.has_service("xtool", "focus")


async def test_action_sets_the_crop_numbers_and_refreshes_once(hass: HomeAssistant) -> None:
    from custom_components.xtool.protocols.ws_v2.entities import _camera_view_values, _crop_numbers
    from custom_components.xtool.services import _async_camera

    coordinator = _f2uv(hass)
    camera = _camera(coordinator, "main")
    numbers = {s: _number(coordinator, f"camera_main_view_{s}") for s in SETTINGS}
    for setting, number in numbers.items():
        number.async_write_ha_state = MagicMock()
        _crop_numbers(coordinator)[setting] = number
    camera.refresh_picture = AsyncMock()
    call = MagicMock()
    call.data = {"crop_width": 267, "crop_center_x": 15}
    await _async_camera(camera, call)
    assert numbers["width"].native_value == 267
    assert numbers["center_x"].native_value == 15
    assert numbers["height"].native_value == 200  # not given: unchanged
    numbers["width"].async_write_ha_state.assert_called_once()
    numbers["height"].async_write_ha_state.assert_not_called()
    camera.refresh_picture.assert_awaited_once()
    assert _camera_view_values(coordinator)["width"] == 267


async def test_action_refuses_other_cameras_and_empty_calls(hass: HomeAssistant) -> None:
    from custom_components.xtool.services import _async_camera

    coordinator = _f2uv(hass)
    call = MagicMock()
    call.data = {"crop_width": 100}
    with pytest.raises(ServiceValidationError):
        await _async_camera(_camera(coordinator, "deep"), call)
    call.data = {}
    with pytest.raises(ServiceValidationError):
        await _async_camera(_camera(coordinator, "main"), call)


async def test_new_crop_takes_a_new_photo(hass: HomeAssistant) -> None:
    coordinator = _f2uv(hass)
    camera = _camera(coordinator, "main")
    camera.hass = hass
    camera.async_write_ha_state = MagicMock()
    await camera.async_camera_image()
    assert coordinator.protocol.camera_snap.await_count == 1
    token = camera.access_tokens[-1]
    await camera.refresh_picture()
    # A cached photo has the old crop: a new one is taken.
    assert coordinator.protocol.camera_snap.await_count == 2
    assert camera.access_tokens[-1] != token
    camera.async_write_ha_state.assert_called_once()


async def test_old_view_entity_ids_become_crop(hass: HomeAssistant, config_entry) -> None:
    from custom_components.xtool import _rename_camera_crop_entities

    coordinator = _f2uv(hass)
    registry = er.async_get(hass)
    for setting in SETTINGS:
        registry.async_get_or_create(
            "number", "xtool", f"{SERIAL}_camera_main_view_{setting}",
            config_entry=config_entry,
            suggested_object_id=(
                "my_own_name" if setting == "center_y"
                else f"xtool_f2_ultra_uv_main_camera_view_{setting}"
            ),
        )
    _rename_camera_crop_entities(hass, config_entry, coordinator)
    ids = {
        setting: registry.async_get_entity_id("number", "xtool", f"{SERIAL}_camera_main_view_{setting}")
        for setting in SETTINGS
    }
    assert ids["width"] == "number.xtool_f2_ultra_uv_main_camera_crop_width"
    assert ids["height"] == "number.xtool_f2_ultra_uv_main_camera_crop_height"
    assert ids["center_x"] == "number.xtool_f2_ultra_uv_main_camera_crop_center_x"
    assert ids["center_y"] == "number.my_own_name"  # chosen by the user: kept


def _perspective_select(coordinator):
    return next(
        s for s in coordinator.build_selects()
        if s.unique_id == f"{SERIAL}_camera_deep_perspective"
    )


def test_perspective_select_is_on_the_deep_camera_device(hass: HomeAssistant) -> None:
    coordinator = _f2uv(hass)
    select = _perspective_select(coordinator)
    assert _identifiers(select) == {("xtool", f"{SERIAL}:camera_deep")}
    assert select.translation_key == "perspective"
    assert select.options == ["uncorrected", "rotated", "straightened"]
    assert select.current_option == "straightened"
    assert select.available
    ids = [s.unique_id for s in coordinator.build_selects() if "perspective" in s.unique_id]
    assert ids == [f"{SERIAL}_camera_deep_perspective"]


def test_only_cameras_with_a_perspective_have_the_select(hass: HomeAssistant) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    for key, model in DEVICE_MODELS.items():
        coordinator = build_coordinator(hass, model)
        has = any("perspective" in (s.unique_id or "") for s in coordinator.build_selects())
        assert has is bool(getattr(model, "camera_perspectives", ())), key


def _deep_photo() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4000, 3000), (200, 200, 200)).save(out, "JPEG")
    return out.getvalue()


async def test_each_perspective_option(hass: HomeAssistant) -> None:
    from PIL import Image

    from custom_components.xtool.protocols.ws_v2.entities import _thumbnail_registry

    coordinator = _f2uv(hass)
    coordinator.protocol.camera_snap = AsyncMock(return_value=_deep_photo())
    deep = _camera(coordinator, "deep")
    deep.hass = hass
    deep.async_write_ha_state = MagicMock()
    select = _perspective_select(coordinator)
    select.async_write_ha_state = MagicMock()
    _thumbnail_registry(coordinator).cameras["deep"] = deep

    # Straightened (the default): the perspective result.
    straightened = Image.open(io.BytesIO(await deep.async_camera_image()))
    assert straightened.size == (1000, 750)

    # Each change takes a new photo. Rotated keeps the photo's shape.
    await select.async_select_option("rotated")
    assert select.current_option == "rotated"
    assert coordinator.protocol.camera_snap.await_count == 2
    rotated = Image.open(io.BytesIO(deep._last_snapshot))
    assert rotated.size[0] == 1000 and rotated.size[1] == pytest.approx(750, abs=2)

    await select.async_select_option("uncorrected")
    assert coordinator.protocol.camera_snap.await_count == 3
    assert Image.open(io.BytesIO(deep._last_snapshot)).size == (4000, 3000)
    deep.async_write_ha_state.assert_called()


def test_upright_turn_has_no_empty_corners() -> None:
    from PIL import Image

    from custom_components.xtool.protocols.ws_v2.camera_correction import rotate_image

    result = Image.open(io.BytesIO(rotate_image(_deep_photo(), 151.5)))
    for corner in ((2, 2), (result.width - 3, 2), (2, result.height - 3), (result.width - 3, result.height - 3)):
        assert min(result.getpixel(corner)) > 150


@pytest.mark.parametrize(
    "path", [ROOT / "strings.json", ROOT / "translations" / "en.json", ROOT / "translations" / "de.json"],
)
def test_select_is_named(path: Path) -> None:
    select = json.loads(path.read_text(encoding="utf-8"))["entity"]["select"]["perspective"]
    assert set(select["state"]) == {"uncorrected", "rotated", "straightened"}
