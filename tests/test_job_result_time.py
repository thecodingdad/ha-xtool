"""F2 Ultra UV Last job time, from the job result push.

Its statistics carry no time, so the sensor is filled from the
``/work/result`` push's ``timeUse`` (seconds).
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.xtool.protocols.base import XtoolDeviceState

from .conftest import build_coordinator
from .features import requires

pytestmark = requires("job_result_time")


def _models():
    from custom_components.xtool.protocols import DEVICE_MODELS

    return DEVICE_MODELS


def _sensors(hass, model):
    return {
        s.entity_description.key: s
        for s in build_coordinator(hass, model).build_sensors()
    }


def test_f2_ultra_uv_has_last_job_time(hass: HomeAssistant) -> None:
    for key, model in _models().items():
        if not (model.model_id.startswith("F2UltraUV") and model.protocol_version == "V2"):
            continue
        sensors = _sensors(hass, model)
        sensor = sensors["last_job_time"]
        state = XtoolDeviceState()
        state.last_job_time_seconds = 15
        assert sensor.entity_description.value_fn(state, None) == 15, key


def test_one_last_job_time_per_laser(hass: HomeAssistant) -> None:
    for key, model in _models().items():
        if model.protocol_version != "V2":
            continue
        ids = [
            s for s in build_coordinator(hass, model).build_sensors()
            if s.entity_description.key == "last_job_time"
        ]
        assert len(ids) <= 1, key


def test_the_push_fills_it() -> None:
    from unittest.mock import AsyncMock

    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    proto.request = AsyncMock(return_value={})
    proto._dispatch_push({
        "url": "/work/result",
        "data": {
            "module": "WORK_RESULT", "type": "WORK_FINISHED",
            "info": {"result": "success", "taskId": "t", "timeUse": 15},
        },
    })
    state = XtoolDeviceState()
    proto._apply_latest_to_state(state)
    assert state.last_job_time_seconds == 15
