"""Tests for the P2's push channel (ws://<laser>:8081), as xTool Studio uses it.

The frame layout and messages were captured from a P2 (firmware 40.21.025)
on 2026-11-07.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from .features import requires

pytestmark = requires("p2_push")

KEEPALIVE = bytes.fromhex("0000000000000a001400")


def frame(obj: dict) -> bytes:
    """A push frame as the P2 sends it: 10-byte header, then the JSON."""
    body = json.dumps(obj, separators=(",", ":")).encode()
    return b"\x00\x00" + len(body).to_bytes(4, "little") + b"\x01\x00\x02\x00" + body


IDLE = {"info": {"mode": "P_IDLE", "subMode": "", "taskId": "c5a7"}, "level": "notice",
        "module": "STATUS_CONTROLLER", "type": "MODE_CHANGE"}
WORKING = {"info": {"mode": "Work", "subMode": "working", "taskId": "t1"}, "level": "notice",
           "module": "STATUS_CONTROLLER", "type": "MODE_CHANGE"}
LID_OPEN = {"info": "", "level": "notice", "module": "GAP", "type": "OPEN"}


def p2():
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    return P2Protocol("127.0.0.1")


def test_decode_frames() -> None:
    """A push decodes to its JSON; the keepalive decodes to nothing."""
    from custom_components.xtool.protocols.rest.p2 import decode_push_frame

    assert decode_push_frame(frame(IDLE)) == IDLE
    assert decode_push_frame(KEEPALIVE) is None
    assert decode_push_frame(json.dumps(LID_OPEN)) == LID_OPEN
    # The header of a captured idle push: the JSON is 0x9a bytes long
    captured = bytes.fromhex("00009a00000001000200")
    assert int.from_bytes(captured[2:6], "little") == 0x9A


@pytest.mark.parametrize(
    ("mode", "sub", "status"),
    [
        ("P_IDLE", "", "idle"), ("P_SLEEP", "", "sleeping"), ("P_WORK_DONE", "", "finished"),
        ("Work", "workReady", "processing_ready"), ("Work", "working", "processing"),
        ("Work", "workPause", "paused"), ("P_MEASURE", "", "measuring"), ("P_UPGRADE", "", "firmware_update"),
    ],
)
def test_modes_as_studio_reads_them(mode: str, sub: str, status: str) -> None:
    """The P2's modes map to the status Studio shows."""
    from custom_components.xtool.protocols.rest.p2 import map_p2_mode

    assert map_p2_mode(mode, sub) == status


def test_pushes_update_the_state() -> None:
    """Mode, lid and button pushes reach the state and the events."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    proto = p2()
    told = []
    proto._push_notify = lambda: told.append(1)
    proto.handle_push(WORKING)
    proto.handle_push(LID_OPEN)
    proto.handle_push({"info": "", "level": "notice", "module": "BUTTON", "type": "SHOERT_PRESS"})
    state = XtoolDeviceState()
    proto.apply_latest(state)
    assert state.status == "processing"
    assert state.task_id == "t1"
    assert state.cover_open is True
    assert proto.take_push_events() == [("button", "short_press", {"raw_type": "SHOERT_PRESS"})]
    assert len(told) == 3


def _fake_reads(proto, monkeypatch, gets: dict, during=None) -> None:
    async def get_json(path, timeout=5.0):
        if during is not None and path == during[0]:
            during[1]()
        return gets.get(path)

    async def post_json(path, data=None, timeout=5.0):
        return None

    monkeypatch.setattr(proto, "_get_json", get_json)
    monkeypatch.setattr(proto, "_post_json", post_json)


async def test_lid_closed_reads_closed(monkeypatch) -> None:
    """/peripheral/gap "off" is a closed lid on the P2, and a paused job reads Paused."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    proto = p2()
    _fake_reads(proto, monkeypatch, {
        "/peripheral/gap": {"state": "off"},
        "/device/runningStatus": {"curMode": {"mode": "Work", "subMode": "workPause"}},
    })
    state = XtoolDeviceState()
    await proto.poll_state(state)
    assert state.cover_open is False
    assert state.status == "paused"


async def test_poll_fails_when_the_laser_does_not_answer(monkeypatch) -> None:
    """A poll the laser did not answer fails, so the laser shows unavailable."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    proto = p2()

    async def offline(path, *args, **kwargs):
        proto._mark_offline(path, TimeoutError())
        return None

    monkeypatch.setattr(proto, "_get_json", offline)
    monkeypatch.setattr(proto, "_post_json", offline)
    with pytest.raises(ConnectionError):
        await proto.poll_state(XtoolDeviceState())


async def test_a_push_during_the_poll_wins(monkeypatch) -> None:
    """The lid opening while a poll runs is not undone by the poll's older read."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    proto = p2()
    _fake_reads(proto, monkeypatch, {"/peripheral/gap": {"state": "off"}},
                during=("/peripheral/gap", lambda: proto.handle_push(LID_OPEN)))
    state = XtoolDeviceState()
    await proto.poll_state(state)
    assert state.cover_open is True


class FakeChannel:
    """Stands in for the WebSocket: hands out the given frames, then stays silent."""

    def __init__(self, frames: list[bytes]) -> None:
        self.frames = list(frames)
        self.sent: list[str] = []
        self.closed = False

    async def send_str(self, text: str) -> None:
        self.sent.append(text)

    async def receive(self, timeout: float | None = None):
        from aiohttp import WSMessage, WSMsgType

        if self.frames:
            return WSMessage(WSMsgType.BINARY, self.frames.pop(0), None)
        await asyncio.sleep(timeout or 0)
        raise asyncio.TimeoutError

    async def close(self) -> None:
        self.closed = True


async def test_channel_pings_reads_and_notices_a_silent_laser(monkeypatch) -> None:
    """Studio's ping goes out, pushes are read, and silence ends the channel."""
    from custom_components.xtool.protocols.rest import p2 as p2_module

    monkeypatch.setattr(p2_module, "PUSH_TIMEOUT_SECONDS", 0.6)
    monkeypatch.setattr(p2_module, "PING_SECONDS", 0.2)
    proto = p2()
    channel = FakeChannel([frame(IDLE), KEEPALIVE, frame(LID_OPEN)])
    proto._ws = channel
    proto._last_frame = p2_module.time.monotonic()
    told: list[bool] = []
    proto._push_notify = lambda: told.append(proto.connected)
    reader = asyncio.create_task(proto._read_pushes(channel))
    await asyncio.sleep(0.3)
    assert proto.connected
    assert channel.sent and channel.sent[0] == "ping"
    assert proto._latest["cover_open"][0] is True
    assert proto._latest["status"][0] == "idle"
    # Silent from here on: the channel ends and says so
    await asyncio.wait_for(reader, 3)
    assert channel.closed
    assert not proto.connected
    assert told and told[-1] is False


def test_p2_uses_the_push_coordinator() -> None:
    """The P2 model is wired to the push protocol and coordinator, with button events."""
    from custom_components.xtool.protocols.rest import XTOOL_P2
    from custom_components.xtool.protocols.rest.coordinator import P2Coordinator
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    assert XTOOL_P2.protocol_class is P2Protocol
    assert XTOOL_P2.coordinator_class is P2Coordinator
    assert XTOOL_P2.has_button_event
