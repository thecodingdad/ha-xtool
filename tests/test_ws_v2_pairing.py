"""WS-V2 pairing by a button press on the device (issue #25)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.const import DOMAIN
from custom_components.xtool.protocols.ws_v2 import protocol as ws_v2

from .fake_ws_v2 import SESSION_TOKEN

NEED_BUTTON = {"code": 406, "message": "need button confirm", "data": {"authState": "WAITING_BUTTON_CONFIRM"}}
ISSUED = {"code": 0, "data": {"authState": "TOKEN_AUTHORIZED", "token": "test-issued", "sessionToken": SESSION_TOKEN}}


@pytest.fixture(autouse=True)
def no_waiting():
    """The auth-status poll waits a second between tries."""
    with patch.object(ws_v2.asyncio, "sleep", AsyncMock()):
        yield


def _auth_status(v2_device, *replies: dict) -> list[dict]:
    """Answer the auth-status polls in turn (the last one repeats)."""
    answers = list(replies)
    original = v2_device.session

    def session():
        s = original()
        ws = v2_device.sockets[-1]
        send = ws.send_bytes

        async def send_bytes(frame):
            url = json.loads(ws_v2._decode_frames(frame)[0][0][1])["url"]
            if url == "/v1/user/auth-status":
                ws.responses[url] = answers.pop(0) if len(answers) > 1 else answers[0]
            await send(frame)

        ws.send_bytes = send_bytes
        return s

    v2_device.session = session
    return answers


async def test_a_button_press_pairs(v2_protocol, v2_device) -> None:
    stored = []
    v2_protocol.pairing_token = ""
    v2_protocol.on_pairing_token = stored.append
    v2_device.replies[0] = NEED_BUTTON
    _auth_status(v2_device, {"code": 406, "message": "waiting"}, ISSUED)
    with patch.object(ws_v2.aiohttp, "ClientSession", side_effect=v2_device.session):
        await v2_protocol.pair(30)
    assert v2_protocol.connected
    assert v2_protocol.pairing_token == "test-issued"
    assert stored == ["test-issued"]
    urls = [p["url"] for p in v2_device.outgoing]
    assert urls[0] == "/v1/user/parity" and urls.count("/v1/user/auth-status") == 2


@pytest.mark.parametrize(("code", "reason"), [(410, "timeout"), (409, "blocked"), (503, "blocked")])
async def test_the_button_window_ends(v2_protocol, v2_device, code, reason) -> None:
    v2_protocol.pairing_token = ""
    v2_device.replies[0] = NEED_BUTTON
    _auth_status(v2_device, {"code": code, "message": "over"})
    with patch.object(ws_v2.aiohttp, "ClientSession", side_effect=v2_device.session):
        with pytest.raises(ws_v2.WSV2PairingFailed) as raised:
            await v2_protocol.pair(30)
    assert raised.value.reason == reason
    assert not v2_protocol.connected


async def test_a_plain_connect_does_not_wait_for_the_button(v2_protocol, v2_device) -> None:
    v2_device.replies[0] = NEED_BUTTON
    with pytest.raises(ws_v2.WSV2PairingRequired):
        await v2_protocol.connect()
    assert "/v1/user/auth-status" not in [p["url"] for p in v2_device.outgoing]


async def test_a_blocked_handshake_is_reported_as_blocked(v2_protocol, v2_device) -> None:
    v2_device.replies[0] = {"code": 409, "message": "blocked"}
    with pytest.raises(ws_v2.WSV2PairingFailed) as raised:
        await v2_protocol.connect()
    assert raised.value.reason == "blocked"


async def test_older_firmware_is_tried_once_without_the_token_field(v2_protocol, v2_device) -> None:
    v2_protocol.pairing_token = ""
    v2_device.replies[0] = {"code": 1, "message": "bad request"}
    v2_device.replies.append({"code": 0, "data": {}})
    await v2_protocol.connect()
    first, second = (p for p in v2_device.outgoing if p["url"] == "/v1/user/parity")
    assert first["data"]["token"] == ""
    assert "token" not in second["data"]
    assert v2_protocol.connected
    # Remembered for the next connection
    await v2_protocol.disconnect()
    v2_device.replies.append({"code": 0, "data": {}})
    await v2_protocol.connect()
    assert "token" not in v2_device.outgoing[-1]["data"]


async def test_a_stored_token_is_not_dropped_on_a_refusal(v2_protocol, v2_device) -> None:
    v2_device.replies[0] = {"code": 1, "message": "bad request"}
    with pytest.raises(ConnectionError):
        await v2_protocol.connect()
    assert len([p for p in v2_device.outgoing if p["url"] == "/v1/user/parity"]) == 1


async def test_a_rotated_token_is_stored_without_a_reload(hass: HomeAssistant) -> None:
    """A new token changes the entry data only; that must not reload it."""
    from custom_components.xtool import _async_options_updated

    entry = MockConfigEntry(domain=DOMAIN, data={"host": "192.0.2.10"}, options={"scan_interval": 5})
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        protocol=SimpleNamespace(host="192.0.2.10"), options_at_setup=dict(entry.options),
    )
    hass.config_entries.async_update_entry(entry, data={**entry.data, "pairing_token": "test-issued"})
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await _async_options_updated(hass, entry)
    reload.assert_not_awaited()
    assert entry.data["pairing_token"] == "test-issued"


async def test_setup_hands_the_token_to_the_protocol_and_stores_a_new_one(hass: HomeAssistant) -> None:
    """The runtime protocol presents the stored token; a token it is issued lands in the entry."""
    import custom_components.xtool as integration

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="MXTEST0001",
        data={"host": "192.0.2.10", "model_key": "F2UltraUV_V2", "serial_number": "MXTEST0001", "pairing_token": "stored"},
    )
    entry.add_to_hass(hass)
    created = []

    def make_protocol(host):
        protocol = MagicMock(spec=ws_v2.WSV2Protocol)
        protocol.host = host
        created.append(protocol)
        return protocol

    model = integration.DEVICE_MODELS["F2UltraUV_V2"]
    with (
        patch.object(model, "protocol_class", side_effect=make_protocol),
        patch.object(model, "coordinator_class", side_effect=RuntimeError("stop after the protocol")),
        pytest.raises(RuntimeError, match="stop after the protocol"),
    ):
        await integration.async_setup_entry(hass, entry)
    protocol = created[0]
    assert protocol.pairing_token == "stored"
    protocol.on_pairing_token("rotated")
    assert entry.data["pairing_token"] == "rotated"
