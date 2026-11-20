"""Tests that the WS-V2 poll does not wake a sleeping laser.

An F2 Ultra UV leaves Sleep when it is asked for its settings
(``/v1/device/configs``). While it sleeps, the slow reads wait, and they
run on the first poll after it wakes.
"""

from __future__ import annotations

from .features import requires

SLOW_READS = ("/v1/device/configs", "/v1/device/statistics", "/v1/device/alarms")


class FakeLaser:
    """Answers every read, and reports the mode it is given."""

    def __init__(self) -> None:
        self.mode = "P_SLEEP"
        self.urls: list[str] = []

    async def __call__(self, url: str, method: str = "GET", params=None, data=None, **kwargs):
        self.urls.append(url)
        if url == "/v1/device/runtime-infos":
            return {"curMode": {"mode": self.mode}}
        return {}


def protocol(laser: FakeLaser):
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    proto._connected = True
    proto.request = laser
    return proto


async def poll(proto, times: int) -> None:
    from custom_components.xtool.protocols.base import XtoolDeviceState

    for _ in range(times):
        await proto.poll_state(XtoolDeviceState())


@requires("sleep_no_slow_reads")
async def test_no_slow_reads_while_asleep() -> None:
    """Thirteen polls of a sleeping laser read no settings, statistics or alarms."""
    laser = FakeLaser()
    proto = protocol(laser)
    await poll(proto, 13)
    assert "/v1/device/runtime-infos" in laser.urls
    assert not [u for u in laser.urls if u in SLOW_READS]


@requires("sleep_no_slow_reads")
async def test_slow_reads_on_the_first_poll_after_waking() -> None:
    """The first poll after the laser wakes reads its settings, statistics and alarms."""
    laser = FakeLaser()
    proto = protocol(laser)
    await poll(proto, 4)
    laser.mode = "P_IDLE"
    laser.urls.clear()
    await poll(proto, 1)
    assert all(u in laser.urls for u in SLOW_READS)


async def test_slow_reads_while_awake() -> None:
    """An idle laser has its settings read on the first poll and every sixth one."""
    laser = FakeLaser()
    laser.mode = "P_IDLE"
    proto = protocol(laser)
    await poll(proto, 7)
    assert laser.urls.count("/v1/device/configs") == 2
