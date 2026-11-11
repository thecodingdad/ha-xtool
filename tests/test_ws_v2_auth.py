"""WS-V2 authentication regressions; run with Home Assistant installed."""

import asyncio
from collections import deque
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from homeassistant.exceptions import ConfigEntryAuthFailed

# Bootstrap protocols before integration setup, as Home Assistant's loader does.
_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "xtool"
if "custom_components.xtool" not in sys.modules:
    _package = ModuleType("custom_components.xtool")
    _package.__path__ = [str(_ROOT)]
    sys.modules[_package.__name__] = _package
protocols = importlib.import_module("custom_components.xtool.protocols")
if not hasattr(protocols, "validate_connection"):
    protocols = importlib.reload(protocols)

from custom_components.xtool import config_flow
from custom_components.xtool.const import CONF_PAIRING_TOKEN
from custom_components.xtool.protocols.base import ConnectionInfo, DeviceInfo
from custom_components.xtool.protocols.ws_v2 import protocol as ws_v2
from custom_components.xtool.protocols.ws_v2.coordinator import WSV2Coordinator
from custom_components.xtool.protocols.ws_v2.protocol_dt002 import DT002WSV2Protocol


PAIRING_TOKEN = "test-permanent-credential"
SESSION_TOKEN = "test-session-credential"


class _WebSocket:
    def __init__(self, replies, outgoing, ping_received):
        self.closed = False
        self.closed_event = asyncio.Event()
        self.replies = replies
        self.responses = {}
        self.outgoing = outgoing
        self.ping_received = ping_received
        self.incoming = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.incoming.get()

    async def send_bytes(self, frame):
        frames, remainder = ws_v2._decode_frames(frame)
        assert not remainder and len(frames) == 1
        payload = json.loads(frames[0][1])
        self.outgoing.append(payload)
        if payload["url"] == "/v1/user/parity":
            response = self.replies.popleft()
        else:
            response = self.responses.get(
                payload["url"], {"code": 0, "data": {"ok": True}},
            )
        response = {
            **response,
            "type": "response",
            "transactionId": payload["transactionId"],
        }
        await self.incoming.put(SimpleNamespace(
            type=aiohttp.WSMsgType.BINARY,
            data=ws_v2._encode_frame(ws_v2._json_compact(response).encode()),
        ))
        if payload["url"] == "/v1/user/ping":
            self.ping_received.set()

    async def close(self):
        self.closed = True
        self.closed_event.set()


class ProtocolAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.protocol = ws_v2.WSV2Protocol("printer.test")
        self.protocol.set_pairing_token(PAIRING_TOKEN)
        self.replies = deque([{
            "code": 0,
            "data": {
                "authState": "TOKEN_AUTHORIZED",
                "sessionToken": SESSION_TOKEN,
                "token": "test-returned-permanent-credential",
            },
        }])
        self.outgoing = []
        self.sockets = []
        self.ping_received = asyncio.Event()
        self.session_patch = patch.object(
            ws_v2.aiohttp, "ClientSession", side_effect=self._make_session,
        )
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)

    def _make_session(self):
        ws = _WebSocket(self.replies, self.outgoing, self.ping_received)
        self.sockets.append(ws)
        session = SimpleNamespace(
            closed=False, ws_connect=AsyncMock(return_value=ws),
        )

        async def close():
            session.closed = True

        session.close = close
        return session

    async def asyncTearDown(self):
        await self.protocol.disconnect()

    async def test_authenticated_requests_and_logs(self):
        with self.assertLogs(ws_v2.__name__, level="DEBUG") as logs:
            await self.protocol.request("/v1/project/device/ink-tank")
        parity, request = self.outgoing
        self.assertEqual(parity["data"]["token"], PAIRING_TOKEN)
        self.assertNotIn("sessionToken", parity)
        self.assertEqual(request["sessionToken"], SESSION_TOKEN)
        self.assertNotIn("token", request["data"])
        rendered = "\n".join(logs.output)
        for secret in (
            PAIRING_TOKEN, SESSION_TOKEN, "test-returned-permanent-credential",
        ):
            self.assertNotIn(secret, rendered)

    async def test_legacy_firmware_still_connects_without_session(self):
        self.protocol.set_pairing_token("")
        self.replies.clear()
        for data in ({}, {"sessionToken": None}, {"sessionToken": ""}):
            with self.subTest(data=data):
                self.replies.append({"code": 0, "data": data})
                await self.protocol.request("/v1/device/machineInfo")
                self.assertEqual(self.outgoing[-2]["data"]["token"], "")
                self.assertNotIn("sessionToken", self.outgoing[-1])
                self.assertTrue(self.protocol.connected)
                await self.protocol.disconnect()

    async def test_reconnect_discards_session_but_keeps_pairing_token(self):
        await self.protocol.connect()
        await self.protocol.disconnect()
        self.assertEqual(self.protocol._session_token, "")
        self.replies.append({
            "code": 0, "data": {"sessionToken": "test-new-session"},
        })
        await self.protocol.request("/v1/project/device/ink-tank")
        self.assertEqual(self.outgoing[1]["data"]["token"], PAIRING_TOKEN)
        self.assertNotIn("sessionToken", self.outgoing[1])
        self.assertEqual(self.outgoing[2]["sessionToken"], "test-new-session")
        self.assertTrue(self.sockets[0].closed)

    async def test_rejected_handshake_closes_socket_without_leaking_token(self):
        self.replies[0] = {
            "code": 406, "message": PAIRING_TOKEN,
            "data": {"authState": "DENIED"},
        }
        with self.assertLogs(ws_v2.__name__, level="DEBUG") as logs:
            with self.assertRaises(ws_v2.WSV2AuthenticationError) as raised:
                await self.protocol.connect()
        self.assertNotIn(PAIRING_TOKEN, str(raised.exception))
        self.assertNotIn(PAIRING_TOKEN, "\n".join(logs.output))
        self.assertFalse(self.protocol.connected)
        self.assertTrue(self.sockets[0].closed)
        self.assertIsNone(self.protocol._heartbeat_task)
        self.assertEqual(len(self.outgoing), 1)

    async def test_new_firmware_requires_session_on_success(self):
        self.replies[0] = {
            "code": 0, "data": {"authState": "TOKEN_AUTHORIZED"},
        }
        with self.assertRaises(ws_v2.WSV2AuthenticationError):
            await self.protocol.connect()
        self.assertFalse(self.protocol.connected)

    async def test_concurrent_requests_wait_for_authentication(self):
        await asyncio.gather(
            self.protocol.request("/first"),
            self.protocol.request("/second"),
        )
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(self.outgoing[0]["url"], "/v1/user/parity")
        for request in self.outgoing[1:]:
            self.assertEqual(request["sessionToken"], SESSION_TOKEN)

    async def test_closed_reader_cannot_be_marked_authenticated(self):
        async def closing_reader():
            message = await self.protocol._ws_instr.__anext__()
            self.protocol._handle_binary(message.data)

        with patch.object(self.protocol, "_reader_loop", closing_reader):
            with self.assertRaisesRegex(ConnectionError, "closed during authentication"):
                await self.protocol.connect()
        self.assertFalse(self.protocol.connected)
        self.assertTrue(self.sockets[0].closed)

    async def test_heartbeat_carries_session_token(self):
        with patch.object(ws_v2, "WSV2_HEARTBEAT_SECONDS", 0):
            await self.protocol.connect()
            await asyncio.wait_for(self.ping_received.wait(), timeout=1)
        ping = next(p for p in self.outgoing if p["url"] == "/v1/user/ping")
        self.assertEqual(ping["sessionToken"], SESSION_TOKEN)

    async def test_rejected_heartbeat_forces_new_authentication(self):
        with patch.object(ws_v2, "WSV2_HEARTBEAT_SECONDS", 0):
            await self.protocol.connect()
            self.sockets[0].responses["/v1/user/ping"] = {
                "code": -2, "message": "need user auth",
            }
            await asyncio.wait_for(self.sockets[0].closed_event.wait(), timeout=1)
        self.assertFalse(self.protocol.connected)
        self.assertEqual(self.protocol._session_token, "")
        self.replies.append({
            "code": 0, "data": {"sessionToken": "test-renewed-session"},
        })
        await self.protocol.request("/v1/project/device/ink-tank")
        self.assertEqual(self.outgoing[-1]["sessionToken"], "test-renewed-session")


class ValidationAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_printer_does_not_swallow_authentication_errors(self):
        protocol = DT002WSV2Protocol("printer.test")
        protocol.request = AsyncMock(side_effect=ws_v2.WSV2AuthenticationError())
        with self.assertRaises(ws_v2.WSV2AuthenticationError):
            await protocol._get("/v1/project/device/ink-tank")
        with self.assertRaises(ws_v2.WSV2AuthenticationError):
            await protocol.get_device_info()

    async def test_manual_validation_passes_credential_and_preserves_auth_error(self):
        protocol = ws_v2.WSV2Protocol("printer.test")
        protocol.connect = AsyncMock(side_effect=ws_v2.WSV2AuthenticationError())
        protocol.disconnect = AsyncMock()
        model = SimpleNamespace(
            protocol_class=Mock(return_value=protocol),
            model_id="TestPrinter", protocol_version="V2",
        )
        with patch.dict(protocols.DEVICE_MODELS, {"TestPrinter_V2": model}):
            result = await protocols.validate_with_model(
                "printer.test", "TestPrinter", "V2", pairing_token=PAIRING_TOKEN,
            )
        self.assertEqual(result, protocols.VALIDATION_ERROR_AUTH_REQUIRED)
        self.assertEqual(protocol._pairing_token, PAIRING_TOKEN)
        protocol.disconnect.assert_awaited_once()

    async def test_discovered_validation_passes_credential(self):
        protocol = ws_v2.WSV2Protocol("printer.test")
        protocol.connect = AsyncMock()
        protocol.disconnect = AsyncMock()
        protocol.get_device_info = AsyncMock(return_value=DeviceInfo(
            device_name="Test Printer", serial_number="test-serial",
        ))
        protocol.get_version = AsyncMock(return_value="test-firmware")
        model = SimpleNamespace(
            protocol_class=Mock(return_value=protocol), model_id="TestPrinter",
            protocol_version="V2", name="Test Printer",
        )
        with (
            patch.object(protocols, "identify_host", AsyncMock(return_value=SimpleNamespace(
                name="Test Printer", protocol_version="V2",
            ))),
            patch.object(protocols, "detect_models", return_value=[model]),
        ):
            result = await protocols.validate_connection(
                "printer.test", pairing_token=PAIRING_TOKEN,
            )
        self.assertIsInstance(result, ConnectionInfo)
        self.assertEqual(protocol._pairing_token, PAIRING_TOKEN)
        protocol.disconnect.assert_awaited_once()

    async def test_coordinator_requests_reauthentication(self):
        coordinator = object.__new__(WSV2Coordinator)
        coordinator.data = None
        coordinator.protocol = SimpleNamespace(
            connected=False,
            connect=AsyncMock(side_effect=ws_v2.WSV2AuthenticationError()),
            disconnect=AsyncMock(),
        )
        with self.assertRaises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        coordinator.protocol.disconnect.assert_awaited_once()


class ConfigFlowAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flow = config_flow.XtoolConfigFlow()
        self.flow.context = {}
        self.flow.async_set_unique_id = AsyncMock()
        self.flow._abort_if_unique_id_configured = Mock()
        self.info = ConnectionInfo(
            host="printer.test", name="Test Printer", serial_number="test-serial",
            model_id="TestPrinter", protocol_version="V2",
        )

    async def test_manual_setup_prompts_then_stores_permanent_credential(self):
        with patch.object(config_flow, "validate_connection", AsyncMock(
            side_effect=[protocols.VALIDATION_ERROR_AUTH_REQUIRED, self.info],
        )) as validate:
            prompt = await self.flow.async_step_manual({"host": "printer.test"})
            self.assertEqual(prompt["step_id"], "auth")
            result = await self.flow.async_step_auth({
                CONF_PAIRING_TOKEN: f" {PAIRING_TOKEN} ",
            })
        self.assertEqual(result["data"][CONF_PAIRING_TOKEN], PAIRING_TOKEN)
        self.assertEqual(result["data"]["model_key"], "TestPrinter_V2")
        validate.assert_awaited_with("printer.test", pairing_token=PAIRING_TOKEN)

    async def test_discovery_routes_auth_failure_to_token_form(self):
        with patch.object(config_flow, "validate_connection", AsyncMock(
            return_value=protocols.VALIDATION_ERROR_AUTH_REQUIRED,
        )):
            result = await self.flow.async_step_discovery({"host": "printer.test"})
        self.assertEqual(result["step_id"], "auth")
        self.assertEqual(self.flow._fallback_host, "printer.test")

    async def test_manual_model_selection_is_retained_for_token_validation(self):
        with patch.object(config_flow, "validate_with_model", AsyncMock(
            side_effect=[protocols.VALIDATION_ERROR_AUTH_REQUIRED, self.info],
        )) as validate:
            prompt = await self.flow.async_step_pick_model({
                "host": "printer.test", "model": "TestPrinter_V2",
            })
            self.assertEqual(prompt["step_id"], "auth")
            result = await self.flow.async_step_auth({CONF_PAIRING_TOKEN: PAIRING_TOKEN})
        self.assertEqual(result["data"][CONF_PAIRING_TOKEN], PAIRING_TOKEN)
        validate.assert_awaited_with(
            "printer.test", "TestPrinter", "V2", pairing_token=PAIRING_TOKEN,
        )

    async def test_invalid_or_empty_token_does_not_create_entry(self):
        self.flow._fallback_host = "printer.test"
        with patch.object(config_flow, "validate_connection", AsyncMock(
            return_value=protocols.VALIDATION_ERROR_AUTH_REQUIRED,
        )) as validate:
            for token in ("bad-token", "  "):
                result = await self.flow.async_step_auth({CONF_PAIRING_TOKEN: token})
                self.assertEqual(result["step_id"], "auth")
                self.assertEqual(result["errors"]["base"], "auth_required")
        validate.assert_awaited_once()

    async def test_reauth_updates_existing_entry_and_reloads(self):
        entry = SimpleNamespace(data={
            "host": "printer.test", "model_key": "TestPrinter_V2",
            "serial_number": "test-serial",
        })
        self.flow._get_reauth_entry = Mock(return_value=entry)
        self.flow.async_update_reload_and_abort = Mock(return_value={"type": "abort"})
        await self.flow.async_step_reauth(entry.data)
        with patch.object(config_flow, "validate_with_model", AsyncMock(
            return_value=self.info,
        )):
            await self.flow.async_step_auth({CONF_PAIRING_TOKEN: PAIRING_TOKEN})
        self.flow.async_update_reload_and_abort.assert_called_once_with(
            entry, data_updates={CONF_PAIRING_TOKEN: PAIRING_TOKEN},
            reason="reauth_successful",
        )

    async def test_reauth_rejects_different_device(self):
        self.flow._auth_entry = SimpleNamespace(data={"serial_number": "other-device"})
        self.flow._fallback_host = "printer.test"
        with patch.object(config_flow, "validate_connection", AsyncMock(
            return_value=self.info,
        )):
            result = await self.flow.async_step_auth({CONF_PAIRING_TOKEN: PAIRING_TOKEN})
        self.assertEqual(result["reason"], "wrong_device")

    async def test_reconfigure_updates_existing_entry(self):
        entry = SimpleNamespace(data={
            "host": "printer.test", "model_key": "TestPrinter_V2",
            "serial_number": "test-serial",
        })
        self.flow._get_reconfigure_entry = Mock(return_value=entry)
        self.flow.async_update_reload_and_abort = Mock(return_value={"type": "abort"})
        with patch.dict(protocols.DEVICE_MODELS, {
            "TestPrinter_V2": SimpleNamespace(protocol_version="V2"),
        }):
            prompt = await self.flow.async_step_reconfigure()
        self.assertEqual(prompt["step_id"], "auth")
        with patch.object(config_flow, "validate_with_model", AsyncMock(
            return_value=self.info,
        )):
            await self.flow.async_step_auth({CONF_PAIRING_TOKEN: PAIRING_TOKEN})
        self.flow.async_update_reload_and_abort.assert_called_once_with(
            entry, data_updates={CONF_PAIRING_TOKEN: PAIRING_TOKEN},
            reason="reconfigure_successful",
        )

    async def test_legacy_setup_does_not_store_empty_credential(self):
        with patch.object(config_flow, "validate_connection", AsyncMock(
            return_value=self.info,
        )):
            result = await self.flow.async_step_manual({"host": "printer.test"})
        self.assertNotIn(CONF_PAIRING_TOKEN, result["data"])


class RuntimeAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_credential_reaches_runtime_protocol(self):
        integration = importlib.reload(
            importlib.import_module("custom_components.xtool")
        )
        protocol = DT002WSV2Protocol("printer.test")
        model = SimpleNamespace(
            model_id="TestPrinter", protocol_version="V2",
            protocol_class=Mock(return_value=protocol),
            coordinator_class=Mock(side_effect=RuntimeError("stop before connecting")),
        )
        entry = SimpleNamespace(
            data={
                "host": "printer.test", "model_key": "TestPrinter_V2",
                CONF_PAIRING_TOKEN: PAIRING_TOKEN,
            },
            options={},
        )
        with patch.dict(integration.DEVICE_MODELS, {"TestPrinter_V2": model}):
            with self.assertRaisesRegex(RuntimeError, "stop before connecting"):
                await integration.async_setup_entry(SimpleNamespace(), entry)
        self.assertEqual(protocol._pairing_token, PAIRING_TOKEN)


if __name__ == "__main__":
    unittest.main()
