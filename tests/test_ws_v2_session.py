"""WS-V2 parity, session token and refused sessions, against a fake device.

Adapted from the tests Jenna Massardo wrote for PR #62 (validated on an
O1, 40.101.014.01.ht11).
"""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.xtool.protocols.ws_v2 import protocol as ws_v2

PAIRING_TOKEN = "test-permanent-credential"
SESSION_TOKEN = "test-session-credential"


class FakeWebSocket:
    """Answers parity from a queue and every other request from ``responses``."""

    def __init__(self, replies: deque, outgoing: list, ping_received: asyncio.Event) -> None:
        self.closed = False
        self.closed_event = asyncio.Event()
        self.replies = replies
        self.responses: dict[str, dict] = {}
        self.outgoing = outgoing
        self.ping_received = ping_received
        self.incoming: asyncio.Queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.incoming.get()

    async def send_bytes(self, frame: bytes) -> None:
        frames, remainder = ws_v2._decode_frames(frame)
        assert not remainder and len(frames) == 1
        payload = json.loads(frames[0][1])
        self.outgoing.append(payload)
        if payload["url"] == "/v1/user/parity":
            response = self.replies.popleft()
        else:
            response = self.responses.get(payload["url"], {"code": 0, "data": {"ok": True}})
        response = {**response, "type": "response", "transactionId": payload["transactionId"]}
        await self.incoming.put(SimpleNamespace(
            type=aiohttp.WSMsgType.BINARY,
            data=ws_v2._encode_frame(ws_v2._json_compact(response).encode()),
        ))
        if payload["url"] == "/v1/user/ping":
            self.ping_received.set()

    async def close(self) -> None:
        self.closed = True
        self.closed_event.set()


class Device:
    """The fake device: the parity replies, what was sent, the sockets opened."""

    def __init__(self) -> None:
        self.replies: deque = deque([{
            "code": 0,
            "data": {
                "authState": "TOKEN_AUTHORIZED",
                "sessionToken": SESSION_TOKEN,
                "token": "test-returned-permanent-credential",
            },
        }])
        self.outgoing: list[dict] = []
        self.sockets: list[FakeWebSocket] = []
        self.ping_received = asyncio.Event()

    def session(self) -> SimpleNamespace:
        ws = FakeWebSocket(self.replies, self.outgoing, self.ping_received)
        self.sockets.append(ws)
        session = SimpleNamespace(closed=False, ws_connect=AsyncMock(return_value=ws))

        async def close() -> None:
            session.closed = True

        session.close = close
        return session


@pytest.fixture
async def device():
    device = Device()
    with patch.object(ws_v2.aiohttp, "ClientSession", side_effect=device.session):
        yield device


@pytest.fixture
async def protocol(device):
    protocol = ws_v2.WSV2Protocol("printer.test")
    protocol.pairing_token = PAIRING_TOKEN
    yield protocol
    await protocol.disconnect()


async def test_requests_carry_the_session_and_logs_hide_the_tokens(protocol, device, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=ws_v2.__name__)
    await protocol.request("/v1/project/device/ink-tank")
    parity, request = device.outgoing
    assert parity["data"]["token"] == PAIRING_TOKEN
    assert "sessionToken" not in parity
    assert request["sessionToken"] == SESSION_TOKEN
    for secret in (PAIRING_TOKEN, SESSION_TOKEN, "test-returned-permanent-credential"):
        assert secret not in caplog.text


@pytest.mark.parametrize("data", [{}, {"sessionToken": None}, {"sessionToken": ""}])
async def test_older_firmware_connects_without_a_session(protocol, device, data) -> None:
    protocol.pairing_token = ""
    device.replies[0] = {"code": 0, "data": data}
    await protocol.request("/v1/device/machineInfo")
    assert device.outgoing[0]["data"]["token"] == ""
    assert "sessionToken" not in device.outgoing[1]
    assert protocol.connected


async def test_a_new_connection_gets_a_new_session(protocol, device) -> None:
    await protocol.connect()
    await protocol.disconnect()
    assert protocol._session_token == ""
    device.replies.append({"code": 0, "data": {"sessionToken": "test-new-session"}})
    await protocol.request("/v1/project/device/ink-tank")
    assert device.outgoing[1]["data"]["token"] == "test-returned-permanent-credential"
    assert device.outgoing[2]["sessionToken"] == "test-new-session"
    assert device.sockets[0].closed


@pytest.mark.parametrize("reply", [
    {"code": 406, "message": "need button confirm", "data": {"authState": "DENIED"}},
    {"code": 0, "data": {"authState": "DENIED"}},
    {"code": 0, "data": {"authState": "WAITING_BUTTON_CONFIRM"}},
])
async def test_a_refused_handshake_asks_for_pairing(protocol, device, caplog, reply) -> None:
    caplog.set_level(logging.DEBUG, logger=ws_v2.__name__)
    device.replies[0] = reply
    with pytest.raises(ws_v2.WSV2PairingRequired) as raised:
        await protocol.connect()
    assert PAIRING_TOKEN not in str(raised.value)
    assert PAIRING_TOKEN not in caplog.text
    assert not protocol.connected
    assert device.sockets[0].closed
    assert protocol._heartbeat_task is None


async def test_pairing_waits_when_the_handshake_answers_waiting(protocol, device) -> None:
    device.replies[0] = {"code": 0, "data": {"authState": "WAITING_BUTTON_CONFIRM"}}
    protocol.pairing_token = ""
    with patch.object(ws_v2.asyncio, "sleep", AsyncMock()):
        ws_responses = {"/v1/user/auth-status": {"code": 0, "data": {"token": "test-issued", "sessionToken": SESSION_TOKEN}}}
        original = device.session

        def session():
            s = original()
            device.sockets[-1].responses.update(ws_responses)
            return s

        with patch.object(ws_v2.aiohttp, "ClientSession", side_effect=session):
            await protocol.pair(30)
    assert protocol.connected
    assert protocol.pairing_token == "test-issued"
    assert "/v1/user/auth-status" in [p["url"] for p in device.outgoing]


async def test_concurrent_requests_wait_for_the_handshake(protocol, device) -> None:
    await asyncio.gather(protocol.request("/first"), protocol.request("/second"))
    assert len(device.sockets) == 1
    assert device.outgoing[0]["url"] == "/v1/user/parity"
    assert all(p["sessionToken"] == SESSION_TOKEN for p in device.outgoing[1:])


async def test_a_socket_closed_during_the_handshake_is_not_connected(protocol, device) -> None:
    async def closing_reader():
        message = await protocol._ws_instr.__anext__()
        protocol._handle_binary(message.data)
        protocol._ws_instr.closed = True

    with patch.object(protocol, "_reader_loop", closing_reader):
        with pytest.raises(ConnectionError, match="closed during the handshake"):
            await protocol.connect()
    assert not protocol.connected


async def test_the_heartbeat_carries_the_session(protocol, device) -> None:
    with patch.object(ws_v2, "WSV2_HEARTBEAT_SECONDS", 0):
        await protocol.connect()
        await asyncio.wait_for(device.ping_received.wait(), timeout=1)
    ping = next(p for p in device.outgoing if p["url"] == "/v1/user/ping")
    assert ping["sessionToken"] == SESSION_TOKEN


async def test_a_refused_heartbeat_starts_a_new_session(protocol, device) -> None:
    with patch.object(ws_v2, "WSV2_HEARTBEAT_SECONDS", 0):
        await protocol.connect()
        device.sockets[0].responses["/v1/user/ping"] = {"code": -2, "message": "need user auth"}
        await asyncio.wait_for(device.sockets[0].closed_event.wait(), timeout=1)
    assert not protocol.connected
    device.replies.append({"code": 0, "data": {"sessionToken": "test-renewed-session"}})
    await protocol.request("/v1/project/device/ink-tank")
    assert device.outgoing[-1]["sessionToken"] == "test-renewed-session"


async def test_an_other_heartbeat_error_keeps_the_connection(protocol, device) -> None:
    with patch.object(ws_v2, "WSV2_HEARTBEAT_SECONDS", 0):
        await protocol.connect()
        device.sockets[0].responses["/v1/user/ping"] = {"code": 1, "message": "failed"}
        device.ping_received.clear()
        await asyncio.wait_for(device.ping_received.wait(), timeout=1)
        await asyncio.sleep(0)
    assert protocol.connected
    assert not device.sockets[0].closed


@pytest.mark.parametrize("reply", [
    {"code": -2, "message": "need user auth"},
    {"code": 401, "message": "unauthorized"},
    {"code": 403, "message": "forbidden"},
])
async def test_a_refused_request_asks_for_pairing_once(protocol, device, reply) -> None:
    await protocol.connect()
    device.sockets[0].responses["/v1/project/device/ink-tank"] = reply
    with pytest.raises(ws_v2.WSV2PairingRequired):
        await protocol.request("/v1/project/device/ink-tank")
    with pytest.raises(ws_v2.WSV2PairingRequired):
        protocol.raise_if_auth_rejected()
    protocol.raise_if_auth_rejected()


async def test_an_other_request_error_is_not_a_pairing_matter(protocol, device) -> None:
    await protocol.connect()
    device.sockets[0].responses["/v1/device/statistics"] = {"code": -2, "message": "not support"}
    with pytest.raises(ws_v2.WSV2RequestError):
        await protocol.request("/v1/device/statistics")
    protocol.raise_if_auth_rejected()


async def test_the_coordinator_starts_reauth_for_a_refused_read(hass) -> None:
    from homeassistant.exceptions import ConfigEntryAuthFailed

    from custom_components.xtool.protocols import DEVICE_MODELS

    from .conftest import build_coordinator

    coordinator = build_coordinator(hass, DEVICE_MODELS["F2UltraUV_V2"])
    coordinator.protocol.connected = True
    coordinator.protocol.poll_state = AsyncMock()
    coordinator.protocol.disconnect = AsyncMock()
    coordinator.protocol.raise_if_auth_rejected = MagicMock(
        side_effect=ws_v2.WSV2PairingRequired("refused"),
    )
    coordinator._device_info_fetched = True
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_poll()
    coordinator.protocol.disconnect.assert_awaited()
