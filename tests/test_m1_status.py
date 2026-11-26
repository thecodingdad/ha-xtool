"""The M1's status from /cnc/status (issue #89, firmware 40.18.026.00.ht3).

The M1 answers its state as a ``STATUS`` P_* code, as Studio's M1 reads it
(``getStatus``); ``P_HUNG`` is its pause.
"""

from __future__ import annotations

import pytest

from custom_components.xtool.const import XtoolStatus
from custom_components.xtool.protocols.rest.protocol import _map_rest_status

# The reply of an M1 (serial replaced)
M1_CNC_STATUS = {
    "CPU_TEMP": "37", "WATER_TEMP": "0.000000", "STATUS": "P_SLEEP",
    "Purifier": "normal", "taskId": "PC_M1_MXTEST0001_1795685502539",
}


def test_the_captured_reply() -> None:
    assert _map_rest_status(M1_CNC_STATUS) is XtoolStatus.SLEEPING


@pytest.mark.parametrize(("code", "status"), [
    ("P_IDLE", XtoolStatus.IDLE),
    ("P_ONLINE_READY_WORK", XtoolStatus.PROCESSING_READY),
    ("P_WORKING", XtoolStatus.PROCESSING),
    ("P_HUNG", XtoolStatus.PAUSED),
    ("P_FINISH", XtoolStatus.FINISHED),
])
def test_studios_m1_codes(code: str, status: XtoolStatus) -> None:
    assert _map_rest_status({**M1_CNC_STATUS, "STATUS": code}) is status


# --- Requested in #89: CPU temperature, progress, camera --------------------


def _m1(replies: dict[str, dict | None]):
    from custom_components.xtool.protocols import DEVICE_MODELS
    from custom_components.xtool.protocols.rest.protocol import RestProtocolM1Legacy

    protocol = RestProtocolM1Legacy("192.0.2.50")
    protocol.set_model(DEVICE_MODELS["M1_V1"])
    sent: list[tuple[str, str]] = []

    async def get_json(path, timeout=5.0):
        sent.append(("GET", path))
        return replies.get(path)

    async def post_json(path, data=None, timeout=5.0):
        sent.append(("POST", path))
        return replies.get(path)

    async def get(path, timeout=5.0):
        sent.append(("GET", path))
        return None

    protocol._get_json = get_json
    protocol._post_json = post_json
    protocol._get = get
    return protocol, sent


async def test_the_cpu_temperature_from_the_status() -> None:
    from custom_components.xtool.protocols.base import XtoolDeviceState

    protocol, _ = _m1({"/cnc/status": M1_CNC_STATUS})
    state = XtoolDeviceState()
    await protocol.poll_state(state)
    assert state.cpu_temp == 37


@pytest.mark.parametrize(("code", "progress"), [("P_WORKING", 42.0), ("P_HUNG", 42.0), ("P_IDLE", None)])
async def test_the_progress_while_a_job_runs(code: str, progress: float | None) -> None:
    """Studio's queryProcessPercent: POST /cnc/data?query=progress → {progress}."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    protocol, sent = _m1({
        "/cnc/status": {**M1_CNC_STATUS, "STATUS": code},
        "/cnc/data?query=progress": {"progress": "42"},
    })
    state = XtoolDeviceState()
    state.task_progress = 10.0
    await protocol.poll_state(state)
    assert state.task_progress == progress
    assert (("POST", "/cnc/data?query=progress") in sent) is (progress is not None)


def test_the_m1_entities(hass, config_entry) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    from .conftest import build_coordinator

    coordinator = build_coordinator(hass, DEVICE_MODELS["M1_V1"])
    sensors = {e.translation_key: e for e in coordinator.build_sensors()}
    assert "task_progress" in sensors
    assert sensors["cpu_temp"].entity_registry_enabled_default is False
    cameras = coordinator.build_cameras()
    assert [c.unique_id.rsplit("_", 1)[-1] for c in cameras] == ["camera"]
    # Studio's captureImage
    assert cameras[0]._snap_path == "/snap?stream=0"
