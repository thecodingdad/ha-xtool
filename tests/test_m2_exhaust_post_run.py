"""The M2's exhaust post-run time is ``smokeFanTimeout`` (Studio's
setFanSmokeExhaustTime); the shared number used to write the F family's
``purifierTimeout``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, call

from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.ws_v2.protocol_m2 import M2WSV2Protocol


async def test_written_as_smoke_fan_timeout_and_kept() -> None:
    protocol = M2WSV2Protocol("192.0.2.20")
    protocol.set_model(DEVICE_MODELS["M2_V2"])
    protocol.request = AsyncMock(return_value={})
    await protocol.set_config("purifierTimeout", 45)
    assert protocol.request.await_args == call(
        "/v1/platform/device/config", "PUT", data={"smokeFanTimeout": 45},
    )
    assert protocol._latest["smoking_fan_duration"] == 45


async def test_other_keys_unchanged() -> None:
    protocol = M2WSV2Protocol("192.0.2.20")
    protocol.request = AsyncMock(return_value={})
    await protocol.set_config("beepEnable", True)
    assert protocol.request.await_args == call(
        "/v1/platform/device/config", "PUT", data={"beepEnable": 1},
    )
    assert protocol._latest["beep_enabled_v2"] is True
