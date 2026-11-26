"""Task time during a job on WS-V2 firmware (from PR #14, on a P3).

The P3 names the elapsed seconds of ``/v1/processing/progress`` ``time``
(``{time, total, value}``, total and value being the progress), and a
task time a push cached earlier (the last job's) used to overwrite the
polled one at the end of every poll.
"""

from __future__ import annotations

from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.base import XtoolDeviceState
from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol


def _protocol(progress: dict) -> WSV2Protocol:
    protocol = WSV2Protocol("192.0.2.30")
    protocol.set_model(DEVICE_MODELS["P3_V2"])
    protocol._connected = True
    replies = {
        "/v1/device/runtime-infos": {"curMode": {"mode": "Work", "subMode": "working", "taskId": "T-1"}},
        "/v1/processing/progress": progress,
    }

    async def request(url, method="GET", **kwargs):
        return replies.get(url, {})

    protocol.request = request
    return protocol


async def test_the_p3_elapsed_time_is_read() -> None:
    protocol = _protocol({"time": 8496, "total": 100, "value": 100})
    state = XtoolDeviceState()
    await protocol.poll_state(state)
    assert state.task_time == 8496


async def test_the_polled_time_wins_over_the_last_jobs() -> None:
    protocol = _protocol({"workingTime": 120})
    protocol._latest["task_time"] = 56  # the last job's, from its result push
    state = XtoolDeviceState()
    await protocol.poll_state(state)
    assert state.task_time == 120
