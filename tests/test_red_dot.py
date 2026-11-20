"""The red dot is assumed on after a V2 laser connects.

The laser always powers on with it lit and does not report it, so the
Red dot switch shows on after each new connection, unless the laser
reports its state, and then follows Home Assistant's commands.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.xtool.protocols.base import XtoolDeviceState


def _f2uv():
    from custom_components.xtool.protocols import DEVICE_MODELS

    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )


def _protocol(reply=None):
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    proto._model = _f2uv()
    proto._connected = True

    async def request(url, method="GET", params=None, data=None, timeout=None):
        if params == {"type": "ir_led"}:
            if reply is None:
                raise RuntimeError("V2 GET returned code 1: failed")
            return reply
        return {}

    proto.request = AsyncMock(side_effect=request)
    return proto


def _connected(proto) -> None:
    """What a new connection sets (the WebSocket itself is not opened)."""
    proto._assume_red_dot_on = True
    proto._red_dot_reported = False


async def test_on_after_each_connection_then_follows_ha() -> None:
    proto = _protocol()
    _connected(proto)
    state = XtoolDeviceState()
    state.ir_led_global = False  # turned off before the laser powered off
    await proto.poll_state(state)
    assert state.ir_led_global is True
    # HA turns it off: the next polls keep that.
    state.ir_led_global = False
    await proto.poll_state(state)
    assert state.ir_led_global is False
    # A new connection assumes on again.
    _connected(proto)
    await proto.poll_state(state)
    assert state.ir_led_global is True


async def test_a_reported_state_wins() -> None:
    proto = _protocol({"state": "off"})
    _connected(proto)
    state = XtoolDeviceState()
    await proto.poll_state(state)
    assert state.ir_led_global is False


def test_connect_sets_the_assumption() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "custom_components" / "xtool"
            / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8")
    # The connection is opened in _open_instruction_ws, or in
    # _open_and_handshake where pairing (#26) split it
    name = ("    async def _open_and_handshake(" if "    async def _open_and_handshake(" in text
            else "    async def _open_instruction_ws(")
    body = text[text.index(name):]
    body = body[: body.find("\n    async def ", 10)]
    assert "self._assume_red_dot_on = True" in body
    assert "self._red_dot_reported = False" in body
