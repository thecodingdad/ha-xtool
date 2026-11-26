"""The P3's job state (issue #87), from a P3 on firmware 40.23.012.2020.01.B14.

The P3 reports a job as ``mode: "Work"`` with the phase in ``subMode``,
as Studio's P3 getCurrentStatus reads it, sends its pushes under
``/work/status`` and ``work/mode_change``, and no ``/work/result``: the
job's duration comes with the mode change to ``P_WORK_DONE``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.xtool.const import XtoolStatus
from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.base import XtoolDeviceState
from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

TASK = "97ef1bff-0000-0000-0000-000000000000"


def _protocol() -> WSV2Protocol:
    protocol = WSV2Protocol("192.0.2.30")
    protocol.set_model(DEVICE_MODELS["P3_V2"])
    return protocol


def _push(protocol: WSV2Protocol, url: str, typ: str, info) -> None:
    protocol._dispatch_push({"url": url, "data": {"module": "STATUS_CONTROLLER", "type": typ, "info": info}})


@pytest.mark.parametrize(("cur_mode", "status"), [
    ({"mode": "Work", "subMode": "workReady", "taskId": TASK}, XtoolStatus.PROCESSING_READY),
    ({"mode": "Work", "subMode": "working", "taskId": TASK}, XtoolStatus.PROCESSING),
    ({"mode": "Work", "subMode": "workPause", "taskId": TASK}, XtoolStatus.PAUSED),
    ({"mode": "P_WORK_DONE", "subMode": "", "taskId": TASK}, XtoolStatus.FINISHED),
])
async def test_the_polled_job_phase(cur_mode: dict, status: XtoolStatus) -> None:
    protocol = _protocol()
    protocol.request = AsyncMock(return_value={"curMode": cur_mode})
    state = XtoolDeviceState()
    await protocol._poll_runtime_status(state)
    assert state.status is status


async def test_an_unknown_work_phase_keeps_the_status() -> None:
    protocol = _protocol()
    protocol.request = AsyncMock(return_value={"curMode": {"mode": "Work", "subMode": "somethingNew"}})
    state = XtoolDeviceState()
    state.status = XtoolStatus.PROCESSING
    await protocol._poll_runtime_status(state)
    assert state.status is XtoolStatus.PROCESSING


def test_a_job_from_the_pushes() -> None:
    """The pushes the P3 sent during a 56 s job."""
    protocol = _protocol()
    _push(protocol, "/work/status", "WORK_PREPARED", "")
    assert protocol._latest["status"] is XtoolStatus.PROCESSING_READY
    _push(protocol, "/work/status", "WORK_STARTED", "")
    assert protocol._latest["status"] is XtoolStatus.PROCESSING
    _push(protocol, "/work/status", "WORK_FINISHED", "")
    _push(protocol, "work/mode_change", "MODE_CHANGE",
          {"mode": "P_WORK_DONE", "subMode": "", "taskId": TASK, "timeUse": 56})
    assert protocol._latest["status"] is XtoolStatus.FINISHED
    assert protocol._latest["task_time"] == 56
    assert protocol._latest["last_job_time_seconds"] == 56
    jobs = [(kind, attrs) for source, kind, attrs in protocol._pending_events if source == "job"]
    assert [kind for kind, _ in jobs] == ["started", "finished"]
    assert jobs[-1][1] == {"task_id": TASK, "duration": 56}


def test_the_mode_change_names_the_phase() -> None:
    protocol = _protocol()
    _push(protocol, "work/mode_change", "MODE_CHANGE", {"mode": "Work", "subMode": "working", "taskId": TASK})
    assert protocol._latest["status"] is XtoolStatus.PROCESSING
    assert protocol._latest["task_id"] == TASK
