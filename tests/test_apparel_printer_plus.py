"""The Apparel Printer Plus (DT003) is an Apparel Printer without a powder loop.

Studio's DT003 bundle uses the DT001 routes, without the powder loop,
the speaker and the ink-stack calibration.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xtool.protocols import DEVICE_MODELS, detect_models

from .conftest import build_coordinator


@pytest.mark.parametrize("version", ["V1", "V2"])
def test_found_by_its_device_code(version: str) -> None:
    plus = DEVICE_MODELS[f"DT003_{version}"]
    found = [m for m in detect_models("xTool DT003") if m.protocol_version == version]
    assert found == [plus]
    assert plus.firmware_content_id == "xTool-apparel-printer-plus-firmware"
    assert plus.protocol_class is DEVICE_MODELS[f"DT001_{version}"].protocol_class


def _binary_sensor_keys(hass: HomeAssistant, model_key: str) -> set[str]:
    coordinator = build_coordinator(hass, DEVICE_MODELS[model_key])
    return {e.unique_id.split("_", 1)[1] for e in coordinator.build_binary_sensors()}


def test_no_powder_loop_on_the_plus(hass: HomeAssistant, config_entry) -> None:
    apparel = _binary_sensor_keys(hass, "DT001_V2")
    plus = _binary_sensor_keys(hass, "DT003_V2")
    assert "powder_loop_running" in apparel
    assert apparel - plus == {"powder_loop_running"}
