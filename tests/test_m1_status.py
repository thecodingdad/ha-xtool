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
