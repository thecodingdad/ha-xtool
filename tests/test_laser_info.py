"""Tests for the F2 Ultra UV Engraving lens, Wi-Fi signal and Calibration
sensors, and the flame record on the Flame alarm switch.

Each is read the way xTool Studio reads it, and nothing is written.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xtool.protocols.base import XtoolDeviceState

from .conftest import build_coordinator
from .features import COMPONENT, has, requires

pytestmark = requires("laser_info")

CALIB_BLOB = json.dumps({"calib_time": "2025-11-09 09:41:58", "data0": "[1084,644]"})


def _f2uv():
    from custom_components.xtool.protocols import DEVICE_MODELS

    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )


def _laser_replies(url, method="GET", params=None, data=None, timeout=None):
    if url == "/v1/peripheral/param" and params == {"type": "cj_check"}:
        assert data == {"action": "get"}
        return {"CJ_DetValue": 1, "CJ_SwitchValue": 1021, "fieldLensType": "big"}
    if url == "/v1/net/wifi_signal_strength":
        assert method == "POST" and data == {"name": "wlan0"}
        return {"reliable": 1, "wifi_signal_strength": -48}
    if url == "/v1/device/configs":
        assert method == "GET"
        keys = data["kv"]
        if keys == ["imgConfigGlobalCalib"]:
            return {"imgConfigGlobalCalib": CALIB_BLOB}
        return {
            "globalOffsetZ": -1.27, "zPositionCompensate": -2.5,
            "zPositionCompensateSmall": 4.75, "zPositionSecondCompensate": 0.0,
            "innerZOffset": -0.82, "laserOffsetUv": "M531X-0.26Y0.01P",
            "laserOffsetBlue": "M531X-0.66Y0.2B", "laserAngleBlue": "M533S0.0B",
            "secondOffsetFlag": True, "flameAlarmRecord": False,
        }
    return {}


def _protocol():
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    proto.request = AsyncMock(side_effect=_laser_replies)
    proto._model = _f2uv()
    return proto


async def test_reads_the_lens_wifi_and_calibration() -> None:
    proto = _protocol()
    await proto._poll_laser_info()
    state = XtoolDeviceState()
    proto._apply_latest_to_state(state)
    assert state.engraving_lens == "big"
    assert state.wifi_signal == -48
    assert state.flame_alarm_record is False
    assert proto.calibration_time.strftime("%Y-%m-%d %H:%M:%S") == "2025-11-09 09:41:58"
    assert proto.calibration_time.tzinfo is not None
    assert proto.calibration_info == {
        "global_offset_z": -1.27,
        "z_position_compensate": -2.5,
        "z_position_compensate_small": 4.75,
        "z_position_second_compensate": 0.0,
        "inner_z_offset": -0.82,
        "laser_offset_uv": {"x": -0.26, "y": 0.01},
        "laser_offset_blue": {"x": -0.66, "y": 0.2},
        "laser_angle_blue": 0.0,
        "second_offset_flag": True,
    }
    assert proto._laser_info_known is True


async def test_the_calibration_grid_is_read_once_per_connection() -> None:
    proto = _protocol()
    await proto._poll_laser_info()
    await proto._poll_laser_info()
    grid_reads = [
        c for c in proto.request.await_args_list
        if c.kwargs.get("data") == {"alias": "config", "type": "user", "kv": ["imgConfigGlobalCalib"]}
    ]
    assert len(grid_reads) == 1


async def test_nothing_but_reads_is_sent() -> None:
    proto = _protocol()
    await proto._poll_laser_info()
    for call in proto.request.await_args_list:
        url, method = call.args[0], call.args[1]
        assert method == "GET" or url == "/v1/net/wifi_signal_strength", call


async def test_read_until_known_then_slowly() -> None:
    proto = _protocol()
    proto._connected = True
    proto._poll_laser_info = AsyncMock()
    proto._poll_counter = 1
    await proto.poll_state(XtoolDeviceState())
    proto._poll_laser_info.assert_awaited_once()
    proto._laser_info_known = True
    proto._poll_laser_info.reset_mock()
    proto._poll_counter = 1
    await proto.poll_state(XtoolDeviceState())
    proto._poll_laser_info.assert_not_awaited()


def test_unknown_values_are_kept_as_they_are() -> None:
    from custom_components.xtool.protocols.ws_v2.protocol import calibration_time, calibration_value

    assert calibration_value("odd") == "odd"
    assert calibration_value(3) == 3
    assert calibration_time("not json") is None
    assert calibration_time(json.dumps({"data0": "[1,2]"})) is None


async def test_entities(hass: HomeAssistant) -> None:
    from homeassistant.components.sensor import SensorDeviceClass

    coordinator = build_coordinator(hass, _f2uv())
    proto = _protocol()
    coordinator.protocol = proto
    await proto._poll_laser_info()
    state = XtoolDeviceState()
    proto._apply_latest_to_state(state)
    coordinator.data = state
    sensors = {s.translation_key: s for s in coordinator.build_sensors()}
    lens = sensors["engraving_lens"]
    assert lens.device_class == SensorDeviceClass.ENUM
    assert lens.options == ["big", "small", "none"]
    assert lens.native_value == "big"
    wifi = sensors["wifi_signal"]
    assert wifi.device_class == SensorDeviceClass.SIGNAL_STRENGTH
    assert wifi.native_unit_of_measurement == "dBm"
    assert wifi.native_value == -48
    calibration = sensors["calibration"]
    assert calibration.device_class == SensorDeviceClass.TIMESTAMP
    assert calibration.native_value == proto.calibration_time
    assert calibration.extra_state_attributes["laser_offset_uv"] == {"x": -0.26, "y": 0.01}
    flame = next(s for s in coordinator.build_switches() if s.translation_key == "flame_alarm_v2")
    assert flame.extra_state_attributes == {"flame_record": False}


def test_only_the_uv_models_have_these(hass: HomeAssistant) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    for key, model in DEVICE_MODELS.items():
        uv = model.model_id.startswith("F2UltraUV") and model.protocol_version == "V2"
        assert getattr(model, "has_laser_info", False) is uv, key
        keys = {s.translation_key for s in build_coordinator(hass, model).build_sensors()}
        assert ({"engraving_lens", "wifi_signal", "calibration"} <= keys) is uv, key


@pytest.mark.parametrize(
    "path", ["strings.json", "translations/en.json", "translations/de.json"],
)
def test_translated(path: str) -> None:
    sensors = json.loads((COMPONENT / path).read_text(encoding="utf-8"))["entity"]["sensor"]
    assert set(sensors["engraving_lens"]["state"]) == {"big", "small", "none"}
    assert {"wifi_signal", "calibration"} <= set(sensors)


@pytest.mark.skipif(not has("connection_first"), reason="branch lacks connection_first")
async def test_read_again_on_the_next_poll_until_the_laser_answers() -> None:
    """A laser that is still booting is asked again on the next poll, not
    only on the slow cadence about 30 seconds later."""
    from custom_components.xtool.protocols.ws_v2.protocol import LASER_INFO_TRIES

    proto = _protocol()
    booting = True

    async def replies(url, method="GET", params=None, data=None, timeout=None):
        if booting and url in ("/v1/peripheral/param", "/v1/net/wifi_signal_strength"):
            raise TimeoutError
        return _laser_replies(url, method, params, data, timeout)

    proto.request = AsyncMock(side_effect=replies)
    await proto._poll_laser_info()
    assert proto._laser_info_known is False
    booting = False
    await proto._poll_laser_info()
    assert proto._laser_info_known is True
    # A laser that never answers stops being asked on every poll
    proto = _protocol()
    proto.request = AsyncMock(side_effect=TimeoutError)
    for _ in range(LASER_INFO_TRIES):
        await proto._poll_laser_info()
    assert proto._laser_info_known is True
