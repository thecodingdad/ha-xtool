"""The F1 Ultra's fill light (issue #27).

Studio's F1 Ultra and F1 Ultra V2 (GS003) bundles keep the brightness in
the ``fillLightBrightness`` config and light the LEDs with ``set_bri`` on
the ``fill_light`` peripheral. F1 Ultra firmware 40.52.018 answers a
write of ``fillLightBrightFront`` with ``code 1: failed``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, call

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.base import XtoolDeviceState
from custom_components.xtool.protocols.ws_v2.entities import WSV2FillLight
from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

from .conftest import build_coordinator


def _protocol(model_key: str) -> WSV2Protocol:
    protocol = WSV2Protocol("192.0.2.10")
    protocol.set_model(DEVICE_MODELS[model_key])
    protocol.request = AsyncMock(return_value={})
    return protocol


def _light(hass: HomeAssistant, model_key: str) -> WSV2FillLight:
    coordinator = build_coordinator(hass, DEVICE_MODELS[model_key])
    coordinator.protocol = _protocol(model_key)
    coordinator.data = XtoolDeviceState()
    coordinator.data.available = True
    light = WSV2FillLight(coordinator)
    light.async_write_ha_state = lambda: None
    return light


def _config(key: str, value: int) -> call:
    return call("/v1/device/configs", "PUT", data={"alias": "config", "type": "user", "kv": {key: value}})


def _set_bri(value: int) -> call:
    return call("/v1/peripheral/param", "PUT", params={"type": "fill_light"},
                data={"action": "set_bri", "idx": 0, "value": value})


@pytest.mark.parametrize("model_key", ["F1Ultra_V2", "GS003_V2"])
async def test_set_as_studio_sets_it(hass: HomeAssistant, model_key: str) -> None:
    light = _light(hass, model_key)
    request = light.coordinator.protocol.request
    await light.async_turn_on(brightness=255)
    assert request.await_args_list == [_config("fillLightBrightness", 255), _set_bri(255)]
    request.reset_mock()
    await light.async_turn_on(brightness=128)  # Studio's 50 %
    assert request.await_args_list == [_config("fillLightBrightness", 136), _set_bri(136)]
    request.reset_mock()
    await light.async_turn_off()
    assert request.await_args_list == [_config("fillLightBrightness", 0), _set_bri(0)]
    assert light.is_on is False


async def test_the_brightness_is_read_from_its_config() -> None:
    protocol = _protocol("F1Ultra_V2")
    protocol.request = AsyncMock(return_value={"fillLightBrightness": 137, "workingMode": "NORMAL"})
    await protocol._poll_configs()
    assert "fillLightBrightness" in protocol.request.await_args.kwargs["data"]["kv"]
    assert protocol._latest["fill_light_a"] == 137
    protocol._dispatch_push({"url": "/device/config", "data": {
        "module": "DEVICE_CONFIG", "type": "INFO", "info": {"fillLightBrightness": 0}}})
    assert protocol._latest["fill_light_a"] == 0


async def test_other_single_fill_lights_are_unchanged(hass: HomeAssistant) -> None:
    """Models without the flag still write both channel keys and never read fillLightBrightness."""
    light = _light(hass, "M1Ultra_V2")
    request = light.coordinator.protocol.request
    await light.async_turn_on(brightness=255)
    assert request.await_args_list == [_config("fillLightBrightFront", 255), _config("fillLightBrightBack", 255)]

    protocol = _protocol("F2UltraUV_V2")
    protocol.request = AsyncMock(return_value={"fillLightBrightness": 137})
    await protocol._poll_configs()
    assert "fillLightBrightness" not in protocol.request.await_args.kwargs["data"]["kv"]
    assert "fill_light_a" not in protocol._latest
