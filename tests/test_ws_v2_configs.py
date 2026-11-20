"""Tests for the WS-V2 device configuration: the poll and the push agree."""

from __future__ import annotations

import pytest

from .features import requires


def protocol():
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    return WSV2Protocol("192.0.2.10")


def push(proto, info: dict) -> None:
    proto._dispatch_push({"url": "/device/config", "data": {"module": "DEVICE_CONFIG", "type": "INFO", "info": info}})


@requires("stops_when_moved_poll")
@pytest.mark.parametrize(("mode", "on"), [("NORMAL", True), ("HANDLE", False)])
def test_stops_when_moved_poll_and_push_agree(mode: str, on: bool) -> None:
    """workingMode NORMAL is on (what the switch writes), from the poll and from a push."""
    proto = protocol()
    proto._apply_configs({"kv": {"workingMode": mode}})
    assert proto._latest["stops_when_moved"] is on
    proto = protocol()
    push(proto, {"workingMode": mode})
    assert proto._latest["stops_when_moved"] is on


def test_flame_alarm_follows_its_own_key() -> None:
    """The flame alarm changes only with flameAlarm, from the poll and from a push."""
    proto = protocol()
    proto._apply_configs({"kv": {"flameAlarm": False, "workingMode": "NORMAL"}})
    assert proto._latest["flame_alarm_v2_enabled"] is False
    push(proto, {"workingMode": "HANDLE"})
    assert proto._latest["flame_alarm_v2_enabled"] is False
    push(proto, {"flameAlarm": True})
    assert proto._latest["flame_alarm_v2_enabled"] is True


class FakeRequests:
    """Answers config reads like the F2 Ultra UV's firmware (or a firmware that wants no body)."""

    def __init__(self, wants_key_list: bool) -> None:
        self.wants_key_list = wants_key_list
        self.bodies: list = []

    async def __call__(self, url: str, method: str = "GET", params=None, data=None, **kwargs):
        self.bodies.append(data)
        if self.wants_key_list and not (isinstance(data, dict) and isinstance(data.get("kv"), list)):
            raise RuntimeError("V2 GET /v1/device/configs returned code 1: [json.exception.out_of_range.403] key 'kv' not found")
        if not self.wants_key_list and data is not None:
            raise RuntimeError("V2 GET /v1/device/configs returned code 1: failed")
        return {"kv": {"flameAlarm": True, "workingMode": "NORMAL", "autoSleepEnable": False}}


@requires("config_read_key_list")
@pytest.mark.parametrize("wants_key_list", [True, False])
async def test_config_read_finds_the_body_the_firmware_wants(wants_key_list: bool) -> None:
    """The read lists its keys (F2 Ultra UV) or sends none (older firmware), and keeps what works."""
    proto = protocol()
    fake = FakeRequests(wants_key_list)
    proto.request = fake
    await proto._poll_configs()
    assert proto._latest["flame_alarm_v2_enabled"] is True
    assert proto._latest["stops_when_moved"] is True
    assert proto._latest["auto_sleep_enable"] is False
    first = len(fake.bodies)
    await proto._poll_configs()
    assert len(fake.bodies) == first + 1  # the body that worked is used straight away
    assert "/v1/device/configs" not in proto._unsupported_endpoints
    sent = fake.bodies[-1]
    if wants_key_list:
        assert sent["alias"] == "config" and "flameAlarm" in sent["kv"] and "workingMode" in sent["kv"]
    else:
        assert sent is None


@requires("config_read_key_list")
def test_settings_start_unknown() -> None:
    """Before the device reports them, the settings are unknown, not off."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    state = XtoolDeviceState()
    for field in ("flame_alarm_v2_enabled", "beep_enabled_v2", "gap_check_enabled", "stops_when_moved", "auto_sleep_enable"):
        assert getattr(state, field) is None, field
