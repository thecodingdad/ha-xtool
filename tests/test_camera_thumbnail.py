"""Tests for the camera thumbnail refresh interval (issue #4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant

from .conftest import SERIAL, build_coordinator
from .features import has, requires

pytestmark = requires("camera_thumbnail_refresh")


def _f2uv(hass: HomeAssistant):
    from custom_components.xtool.protocols import DEVICE_MODELS

    model = next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )
    coordinator = build_coordinator(hass, model)
    coordinator.protocol.camera_snap = AsyncMock(return_value=b"jpeg")
    coordinator.protocol.camera_calibration_file = AsyncMock(return_value=None)
    return coordinator


def _thumbnail_number(coordinator, camera_key: str):
    unique_id = f"{SERIAL}_{camera_key}_thumbnail_refresh"
    return next(n for n in coordinator.build_numbers() if n.unique_id == unique_id)


def test_one_number_per_camera(hass: HomeAssistant) -> None:
    coordinator = _f2uv(hass)
    ids = {
        n.unique_id for n in coordinator.build_numbers()
        if n.unique_id.endswith("_thumbnail_refresh")
    }
    assert ids == {
        f"{SERIAL}_camera_main_thumbnail_refresh",
        f"{SERIAL}_camera_deep_thumbnail_refresh",
    }


def test_number_range(hass: HomeAssistant) -> None:
    coordinator = _f2uv(hass)
    number = _thumbnail_number(coordinator, "camera_main")
    assert (number.native_min_value, number.native_max_value, number.native_step) == (5, 600, 5)
    assert number.native_value == 300
    assert number.available


async def test_token_changes_only_on_the_cameras_schedule(hass: HomeAssistant) -> None:
    coordinator = _f2uv(hass)
    camera = next(c for c in coordinator.build_cameras() if c._camera_name == "main")
    token = camera.access_tokens[-1]
    camera.async_update_token()  # Home Assistant's 5-minute call
    assert camera.access_tokens[-1] == token
    camera.async_write_ha_state = MagicMock()
    await camera._refresh_thumbnail()
    camera.async_write_ha_state.assert_called_once()
    # The photo was taken before the token changed (served from cache).
    assert coordinator.protocol.camera_snap.await_count == 1
    assert camera._last_snapshot == b"jpeg"
    assert camera.access_tokens[-1] != token


async def test_number_restarts_the_camera_timer(hass: HomeAssistant) -> None:
    coordinator = _f2uv(hass)
    camera = next(c for c in coordinator.build_cameras() if c._camera_name == "main")
    number = _thumbnail_number(coordinator, "camera_main")
    camera.hass = hass
    from custom_components.xtool.protocols.ws_v2.entities import _thumbnail_registry

    _thumbnail_registry(coordinator).cameras["main"] = camera
    number._apply(10)
    assert camera._thumbnail_seconds == 10
    assert camera._cancel_thumbnail_timer is not None
    camera._cancel_thumbnail_timer()


def test_translations_name_every_number() -> None:
    import json
    from pathlib import Path

    root = Path("custom_components/xtool")
    for path in (root / "translations" / "en.json", root / "translations" / "de.json", root / "strings.json"):
        numbers = json.loads(path.read_text(encoding="utf-8"))["entity"]["number"]
        # With camera devices, a camera's own device needs no camera name.
        keys = ("camera", "") if has("camera_devices") else ("camera_main", "camera_deep", "camera")
        for key in keys:
            name = f"{key}_thumbnail_refresh" if key else "thumbnail_refresh"
            assert name in numbers, (path, name)


async def test_refresh_stops_when_home_assistant_stops(hass: HomeAssistant, monkeypatch) -> None:
    """A photo every few seconds adds tasks the shutdown waits for, so it never ended."""
    import custom_components.xtool.protocols.ws_v2.entities as entities

    if not has("thumbnail_cancel_on_shutdown"):
        import pytest

        pytest.skip("branch lacks thumbnail_cancel_on_shutdown")
    calls = []
    monkeypatch.setattr(entities, "async_track_time_interval", lambda *a, **kw: calls.append(kw) or (lambda: None))
    coordinator = _f2uv(hass)
    camera = next(c for c in coordinator.build_cameras() if c._camera_name == "main")
    camera.hass = hass
    camera.set_thumbnail_refresh(5)
    assert calls and calls[-1].get("cancel_on_shutdown") is True
