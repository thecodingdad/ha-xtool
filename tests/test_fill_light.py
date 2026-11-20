"""Tests for the WS-V2 fill-light brightness scale (issue #4).

Studio (GS009-CLASS-4 bundle, ``qy``) shows the fill light as a 1-100
percentage of the device value d: ``Math.round((d - 20) * 99 / 235 + 1)``
and writes ``Math.round((p - 1) * 235 / 99 + 20)``. Home Assistant shows
brightness b as ``round(b / 255 * 100)``.
"""

from __future__ import annotations

import math

import pytest

from .features import requires

pytestmark = requires("fill_light_studio_percent")

DEVICE_MIN = 20


def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def studio_percent(device: int) -> int:
    return _js_round((device - DEVICE_MIN) * 99 / (255 - DEVICE_MIN) + 1)


def studio_device(percent: int) -> int:
    return _js_round((percent - 1) * (255 - DEVICE_MIN) / 99 + DEVICE_MIN)


def ha_percent(brightness: int) -> int:
    return _js_round(brightness * 100 / 255)


def convert():
    from custom_components.xtool.protocols.ws_v2 import entities

    return entities._fill_light_device_to_ha, entities._fill_light_ha_to_device


@pytest.mark.parametrize("device", range(DEVICE_MIN, 256))
def test_ha_shows_studios_percentage(device: int) -> None:
    """Every device value reads in HA as the percentage Studio shows."""
    to_ha, _ = convert()
    assert ha_percent(to_ha(device, DEVICE_MIN)) == studio_percent(device)


@pytest.mark.parametrize("percent", range(1, 101))
def test_a_percentage_set_in_ha_writes_what_studio_writes(percent: int) -> None:
    """Setting a percentage in HA sends the value Studio sends for it."""
    _, to_device = convert()
    brightness = _js_round(percent * 255 / 100)
    assert to_device(brightness, DEVICE_MIN) == studio_device(percent)


def test_off_and_linear_models_are_unchanged() -> None:
    to_ha, to_device = convert()
    assert to_ha(0, DEVICE_MIN) == 0
    assert to_device(0, DEVICE_MIN) == 0
    assert to_ha(137, 0) == 137
    assert to_device(137, 0) == 137
