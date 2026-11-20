"""WS-V2 parity, session token and refused sessions, against a fake device.

Adapted from the tests Jenna Massardo wrote for PR #62 (validated on an
O1, 40.101.014.01.ht11).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.xtool.protocols.ws_v2 import protocol as ws_v2

from .fake_ws_v2 import PAIRING_TOKEN, SESSION_TOKEN


async def test_requests_carry_the_session_and_logs_hide_the_tokens(v2_protocol, v2_device, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=ws_v2.__name__)
    await v2_protocol.request("/v1/project/v2_device/ink-tank")
    parity, request = v2_device.outgoing
    assert parity["data"]["token"] == PAIRING_TOKEN
    assert "sessionToken" not in parity
    assert request["sessionToken"] == SESSION_TOKEN
    for secret in (PAIRING_TOKEN, SESSION_TOKEN, "test-returned-permanent-credential"):
        assert secret not in caplog.text


@pytest.mark.parametrize("data", [{}, {"sessionToken": None}, {"sessionToken": ""}])
async def test_older_firmware_connects_without_a_session(v2_protocol, v2_device, data) -> None:
    v2_protocol.pairing_token = ""
    v2_device.replies[0] = {"code": 0, "data": data}
    await v2_protocol.request("/v1/v2_device/machineInfo")
    assert v2_device.outgoing[0]["data"]["token"] == ""
    assert "sessionToken" not in v2_device.outgoing[1]
    assert v2_protocol.connected


async def test_a_new_connection_gets_a_new_session(v2_protocol, v2_device) -> None:
    await v2_protocol.connect()
    await v2_protocol.disconnect()
    assert v2_protocol._session_token == ""
    v2_device.replies.append({"code": 0, "data": {"sessionToken": "test-new-session"}})
    await v2_protocol.request("/v1/project/v2_device/ink-tank")
    assert v2_device.outgoing[1]["data"]["token"] == "test-returned-permanent-credential"
    assert v2_device.outgoing[2]["sessionToken"] == "test-new-session"
    assert v2_device.sockets[0].closed


@pytest.mark.parametrize("reply", [
    {"code": 406, "message": "need button confirm", "data": {"authState": "DENIED"}},
    {"code": 0, "data": {"authState": "DENIED"}},
    {"code": 0, "data": {"authState": "WAITING_BUTTON_CONFIRM"}},
])
async def test_a_refused_handshake_asks_for_pairing(v2_protocol, v2_device, caplog, reply) -> None:
    caplog.set_level(logging.DEBUG, logger=ws_v2.__name__)
    v2_device.replies[0] = reply
    with pytest.raises(ws_v2.WSV2PairingRequired) as raised:
        await v2_protocol.connect()
    assert PAIRING_TOKEN not in str(raised.value)
    assert PAIRING_TOKEN not in caplog.text
    assert not v2_protocol.connected
    assert v2_device.sockets[0].closed
    assert v2_protocol._heartbeat_task is None


async def test_pairing_waits_when_the_handshake_answers_waiting(v2_protocol, v2_device) -> None:
    v2_device.replies[0] = {"code": 0, "data": {"authState": "WAITING_BUTTON_CONFIRM"}}
    v2_protocol.pairing_token = ""
    with patch.object(ws_v2.asyncio, "sleep", AsyncMock()):
        ws_responses = {"/v1/user/auth-status": {"code": 0, "data": {"token": "test-issued", "sessionToken": SESSION_TOKEN}}}
        original = v2_device.session

        def session():
            s = original()
            v2_device.sockets[-1].responses.update(ws_responses)
            return s

        with patch.object(ws_v2.aiohttp, "ClientSession", side_effect=session):
            await v2_protocol.pair(30)
    assert v2_protocol.connected
    assert v2_protocol.pairing_token == "test-issued"
    assert "/v1/user/auth-status" in [p["url"] for p in v2_device.outgoing]


async def test_concurrent_requests_wait_for_the_handshake(v2_protocol, v2_device) -> None:
    await asyncio.gather(v2_protocol.request("/first"), v2_protocol.request("/second"))
    assert len(v2_device.sockets) == 1
    assert v2_device.outgoing[0]["url"] == "/v1/user/parity"
    assert all(p["sessionToken"] == SESSION_TOKEN for p in v2_device.outgoing[1:])


async def test_a_socket_closed_during_the_handshake_is_not_connected(v2_protocol, v2_device) -> None:
    async def closing_reader():
        message = await v2_protocol._ws_instr.__anext__()
        v2_protocol._handle_binary(message.data)
        v2_protocol._ws_instr.closed = True

    with patch.object(v2_protocol, "_reader_loop", closing_reader):
        with pytest.raises(ConnectionError, match="closed during the handshake"):
            await v2_protocol.connect()
    assert not v2_protocol.connected


async def test_the_heartbeat_carries_the_session(v2_protocol, v2_device) -> None:
    with patch.object(ws_v2, "WSV2_HEARTBEAT_SECONDS", 0):
        await v2_protocol.connect()
        await asyncio.wait_for(v2_device.ping_received.wait(), timeout=1)
    ping = next(p for p in v2_device.outgoing if p["url"] == "/v1/user/ping")
    assert ping["sessionToken"] == SESSION_TOKEN


async def test_a_refused_heartbeat_starts_a_new_session(v2_protocol, v2_device) -> None:
    with patch.object(ws_v2, "WSV2_HEARTBEAT_SECONDS", 0):
        await v2_protocol.connect()
        v2_device.sockets[0].responses["/v1/user/ping"] = {"code": -2, "message": "need user auth"}
        await asyncio.wait_for(v2_device.sockets[0].closed_event.wait(), timeout=1)
    assert not v2_protocol.connected
    v2_device.replies.append({"code": 0, "data": {"sessionToken": "test-renewed-session"}})
    await v2_protocol.request("/v1/project/v2_device/ink-tank")
    assert v2_device.outgoing[-1]["sessionToken"] == "test-renewed-session"


async def test_an_other_heartbeat_error_keeps_the_connection(v2_protocol, v2_device) -> None:
    with patch.object(ws_v2, "WSV2_HEARTBEAT_SECONDS", 0):
        await v2_protocol.connect()
        v2_device.sockets[0].responses["/v1/user/ping"] = {"code": 1, "message": "failed"}
        v2_device.ping_received.clear()
        await asyncio.wait_for(v2_device.ping_received.wait(), timeout=1)
        await asyncio.sleep(0)
    assert v2_protocol.connected
    assert not v2_device.sockets[0].closed


@pytest.mark.parametrize("reply", [
    {"code": -2, "message": "need user auth"},
    {"code": 401, "message": "unauthorized"},
    {"code": 403, "message": "forbidden"},
])
async def test_a_refused_request_asks_for_pairing_once(v2_protocol, v2_device, reply) -> None:
    await v2_protocol.connect()
    v2_device.sockets[0].responses["/v1/project/v2_device/ink-tank"] = reply
    with pytest.raises(ws_v2.WSV2PairingRequired):
        await v2_protocol.request("/v1/project/v2_device/ink-tank")
    with pytest.raises(ws_v2.WSV2PairingRequired):
        v2_protocol.raise_if_auth_rejected()
    v2_protocol.raise_if_auth_rejected()


async def test_an_other_request_error_is_not_a_pairing_matter(v2_protocol, v2_device) -> None:
    await v2_protocol.connect()
    v2_device.sockets[0].responses["/v1/v2_device/statistics"] = {"code": -2, "message": "not support"}
    with pytest.raises(ws_v2.WSV2RequestError):
        await v2_protocol.request("/v1/v2_device/statistics")
    v2_protocol.raise_if_auth_rejected()


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
