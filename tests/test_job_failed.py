"""A WS-V2 job the laser reports as failed is a Job "failed" event.

After a power cut stops a job, the laser reports it at boot with
``result: failed`` and ``timeUse: 0``; that is not a finished job.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from custom_components.xtool.const import XtoolStatus

from .features import COMPONENT, requires

pytestmark = requires("job_failed")


def _protocol():
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    proto.request = AsyncMock(return_value={})
    return proto


def _result(proto, result, time_use, task="t-1"):
    proto._dispatch_push({
        "url": "/work/result",
        "data": {
            "module": "WORK_RESULT", "type": "WORK_FINISHED",
            "info": {"result": result, "taskId": task, "timeUse": time_use},
        },
    })


def test_a_failed_job_is_failed() -> None:
    proto = _protocol()
    proto._latest["status"] = XtoolStatus.IDLE
    proto._latest["last_job_time_seconds"] = 15
    _result(proto, "failed", 0)
    assert proto._pending_events == [
        ("job", "failed", {"task_id": "t-1", "duration": 0}),
    ]
    # The Status follows the work mode, and the last good job time stays.
    assert proto._latest["status"] == XtoolStatus.IDLE
    assert proto._latest["last_job_time_seconds"] == 15


def test_a_successful_job_is_finished() -> None:
    proto = _protocol()
    _result(proto, "success", 15)
    assert proto._pending_events == [
        ("job", "finished", {"task_id": "t-1", "duration": 15}),
    ]
    assert proto._latest["status"] == XtoolStatus.FINISHED
    assert proto._latest["last_job_time_seconds"] == 15


def test_the_job_event_offers_failed() -> None:
    from custom_components.xtool.protocols.ws_v2.entities import WSV2_JOB_EVENT_TYPES

    assert "failed" in WSV2_JOB_EVENT_TYPES


@pytest.mark.parametrize(
    "path", ["strings.json", "translations/en.json", "translations/de.json"],
)
def test_translated(path: str) -> None:
    data = json.loads((COMPONENT / path).read_text(encoding="utf-8"))["entity"]
    states = data["event"]["job"]["state_attributes"]["event_type"]["state"]
    assert "failed" in states
