"""Job times are durations, so Home Assistant can show them in h / min (#87)."""

from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant

from custom_components.xtool.protocols import DEVICE_MODELS

from .conftest import build_coordinator

TIMES = {"task_time", "last_job_time", "working_time", "standby_time", "tool_runtime"}


@pytest.mark.parametrize("model_key", sorted(DEVICE_MODELS))
def test_every_time_is_a_duration(hass: HomeAssistant, config_entry, model_key: str) -> None:
    sensors = build_coordinator(hass, DEVICE_MODELS[model_key]).build_sensors()
    times = [s for s in sensors if s.translation_key in TIMES]
    assert times, model_key
    wrong = [s.translation_key for s in times if s.device_class != SensorDeviceClass.DURATION]
    assert not wrong, f"{model_key}: {wrong}"
