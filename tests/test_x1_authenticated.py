"""Offline X1 peer and real HA entities. No device or running HA is contacted."""

import asyncio
import hashlib
import io
import json
import logging
import secrets
import ssl
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import aiohttp
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from PIL import Image

_ROOT = Path.cwd() / "custom_components/xtool"
for _name, _path in (
    ("custom_components.xtool", _ROOT),
    ("custom_components.xtool.protocols", _ROOT / "protocols"),
    ("custom_components.xtool.protocols.ws_v2", _ROOT / "protocols/ws_v2"),
):
    if _name not in sys.modules:
        _package = ModuleType(_name)
        _package.__path__ = [str(_path)]
        sys.modules[_name] = _package

from custom_components.xtool.protocols.ws_v2.coordinator_x1 import X1ReadOnlyCoordinator
from custom_components.xtool.protocols.ws_v2.models import XTOOL_X1_DISCOVERY
from custom_components.xtool.protocols.ws_v2.x1_auth import X1AuthError, X1Grant
from custom_components.xtool.protocols.ws_v2.x1_codec import (
    crc16,
    decode_frames,
    encode_frame,
)
from custom_components.xtool.protocols.ws_v2.x1_data import (
    X1ProtocolError,
    parse_cameras,
    parse_connected,
    parse_modules,
    parse_report,
)
from custom_components.xtool.protocols.ws_v2.x1_transport import X1ReadOnlyProtocol

FIXTURE = json.loads((Path(__file__).parent / "fixtures/x1_readonly.json").read_text())
HOST = "192.0.2.12"
SERIAL = "SYNTHETIC-X1-NOT-REAL"
CLIENT = "00000000-0000-4000-8000-000000000001"


def grant(pin="00" * 32):
    # Generated test values are never credentials or captured device tokens.
    return X1Grant.parse(
        {
            "host": HOST,
            "serial": SERIAL,
            "client_id": CLIENT,
            "consumer": "synthetic-test-consumer",
            "leaf_sha256": pin,
            "user_id": "mk-guest",
            "timezone": "UTC",
            "token": secrets.token_hex(24),
        },
        host=HOST,
        serial=SERIAL,
    )


class ParserTests(unittest.TestCase):
    def test_raw_report_absence_is_unknown_and_not_idle(self):
        self.assertEqual(
            parse_report({}), {"mode": None, "moveState": None, "gohomeState": None}
        )
        self.assertEqual(
            parse_report(FIXTURE["state_report"]["data"]["/v1/platform/device/state"])[
                "mode"
            ],
            "Idle",
        )
        for value in (None, [], {"mode": False}, {"moveState": ""}):
            with self.assertRaises(X1ProtocolError):
                parse_report(value)

    def test_observed_slots_do_not_claim_purifier_or_idle(self):
        modules = parse_modules(FIXTURE["accessory_slots"])
        self.assertTrue(modules[71].online)
        self.assertFalse(modules[82].online)
        self.assertIsNone(modules[71].common_status)
        self.assertEqual(parse_connected(FIXTURE["connected_response"]), ())
        self.assertNotIn(71, parse_modules([]))
        for value in (
            {},
            None,
            [*FIXTURE["accessory_slots"], FIXTURE["accessory_slots"][0]],
        ):
            with self.assertRaises(X1ProtocolError):
                parse_modules(value)
        for field, value in (("status", 1), ("dev_id", True), ("version", {})):
            rows = deepcopy(FIXTURE["accessory_slots"])
            rows[0][field] = value
            with self.assertRaises(X1ProtocolError):
                parse_modules(rows)

    def test_source_only_connected_rows_require_explicit_status_and_hide_addresses(
        self,
    ):
        row = "1,02:00:00:00:00:01,FE-synthetic,1"
        devices = parse_connected({"result": "M9098 " + row})
        self.assertEqual((devices[0].type_prefix, devices[0].connected), ("FE", True))
        self.assertNotIn("02:00", repr(devices))
        for result in (
            "",
            "none",
            "M9098 ",
            "M9098 " + row.rsplit(",", 1)[0],
            "M9098 " + row + ";" + row,
        ):
            with self.assertRaises(X1ProtocolError):
                parse_connected({"result": result})

    def test_camera_lists_reject_missing_malformed_and_duplicate_rows(self):
        cameras = parse_cameras(FIXTURE["camera_list"])
        self.assertEqual(cameras["side"].format, "nv12")
        self.assertIn("ldm", cameras)  # Enumeration is not admission for image capture.
        for value in (None, {}, [*FIXTURE["camera_list"], FIXTURE["camera_list"][0]]):
            with self.assertRaises(X1ProtocolError):
                parse_cameras(value)
        rows = deepcopy(FIXTURE["camera_list"])
        rows[0]["width"] = True
        with self.assertRaises(X1ProtocolError):
            parse_cameras(rows)

    def test_bound_grant_rejects_changes_and_never_discloses_its_fields(self):
        good = grant()
        self.assertNotIn(good.token, repr(good))
        self.assertNotIn(SERIAL, repr(good))
        for key, value in (
            ("host", "192.0.2.13"),
            ("serial", "OTHER-SYNTHETIC"),
            ("client_id", "invalid"),
            ("consumer", ""),
            ("leaf_sha256", ""),
            ("user_id", "cloud-user"),
            ("timezone", "invalid-zone"),
            ("token", ""),
        ):
            data = asdict(good)
            data[key] = value
            with self.assertRaises(X1AuthError) as caught:
                X1Grant.parse(data, host=HOST, serial=SERIAL)
            self.assertNotIn(good.token, str(caught.exception))

    def test_grant_preserves_the_supplied_consumer(self):
        data = asdict(grant())
        data["consumer"] = "synthetic-alternative-consumer"
        parsed = X1Grant.parse(data, host=HOST, serial=SERIAL)
        self.assertEqual(asdict(parsed), data)

    def test_crc_fragmentation_corruption_and_request_flag(self):
        self.assertEqual(crc16(b"123456789"), 0xBB3D)
        raw = encode_frame(b"example")
        self.assertEqual(decode_frames(raw[:8]), ([], raw[:8]))
        self.assertEqual(decode_frames(raw), ([(4, b"example")], b""))
        for index in (0, 8, 10):
            damaged = bytearray(raw)
            damaged[index] ^= 1
            with self.assertRaises(X1ProtocolError):
                decode_frames(bytes(damaged))
        request = encode_frame(b"packet", 33, checksum=False)
        self.assertEqual(request[5:8], b"\xa1\x00\x00")


class OfflinePeerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-offline-peer")]
        )
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .sign(key, hashes.SHA256())
        )
        cert_path, key_path = (
            Path(self.temp.name) / "cert.pem",
            Path(self.temp.name) / "key.pem",
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        self.binding = grant(cert.fingerprint(hashes.SHA256()).hex())
        self.session_token = secrets.token_hex(24)
        self.requests = []
        self.connections = []
        self.file_open = False
        self.report = True
        self.slots = deepcopy(FIXTURE["accessory_slots"])
        self.cameras = deepcopy(FIXTURE["camera_list"])
        self.parity_code = 0
        self.finish_code = 0
        self.fault = None
        self.delay = 0
        self.identity = SERIAL
        self.redirect = False
        self.redirect_followed = False
        self.file_pin_mismatch = False
        output = io.BytesIO()
        Image.new("RGB", (3840, 2160), "#698195").save(output, format="JPEG")
        self.image = output.getvalue()
        app = web.Application()
        app.router.add_get("/websocket", self.peer)
        app.router.add_get("/forbidden", self.forbidden)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0, ssl_context=context)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        original = aiohttp.ClientSession.ws_connect

        def loopback(session, url, **kwargs):
            self.assertEqual(url, f"wss://{HOST}:28900/websocket")
            if self.file_pin_mismatch and kwargs["params"]["function"] == "file_stream":
                kwargs["ssl"] = aiohttp.Fingerprint(bytes(32))
            return original(session, f"wss://127.0.0.1:{port}/websocket", **kwargs)

        self.reroute = patch.object(aiohttp.ClientSession, "ws_connect", new=loopback)
        self.reroute.start()
        self.protocol = X1ReadOnlyProtocol(self.binding)

    async def asyncTearDown(self):
        await self.protocol.disconnect()
        self.reroute.stop()
        await self.runner.cleanup()
        self.temp.cleanup()

    async def forbidden(self, request):
        self.redirect_followed = True
        return web.Response(status=403)

    async def peer(self, request):
        if self.redirect:
            raise web.HTTPFound("/forbidden")
        function = request.query.get("function")
        self.connections.append((function, request.query.get("id")))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if function == "file_stream":
            self.file_open = True
            try:
                async for message in ws:
                    frames, remaining = decode_frames(message.data)
                    if remaining:
                        raise AssertionError("Incomplete test request")
                    for protocol, payload in frames:
                        self.assertEqual(protocol, 33)
                        self.assertEqual(payload[:2], b"\x01\x01")
                        start, length = (
                            int.from_bytes(payload[2:7], "big"),
                            int.from_bytes(payload[7:10], "big"),
                        )
                        offset = start + (1 if self.fault == "offset" else 0)
                        channel = 2 if self.fault == "channel" else 1
                        packet = (
                            bytes([129, channel])
                            + offset.to_bytes(5, "big")
                            + self.image[start : start + length]
                        )
                        raw = encode_frame(packet, 33)
                        if self.fault == "crc":
                            raw = raw[:-1] + bytes([raw[-1] ^ 1])
                        if self.fault == "duplicate":
                            raw += raw
                        await ws.send_bytes(raw)
            finally:
                self.file_open = False
            return ws
        async for message in ws:
            frames, remaining = decode_frames(message.data)
            self.assertFalse(remaining)
            for protocol, payload in frames:
                self.assertEqual(protocol, 4)
                envelope = json.loads(payload)
                self.requests.append(envelope)
                url = envelope["url"]
                code = 0
                if url == "/v1/user/parity":
                    self.assertEqual(envelope["data"]["token"], self.binding.token)
                    self.assertNotIn("sessionToken", envelope)
                    if self.delay:
                        await asyncio.sleep(self.delay)
                        await ws.close()
                        return ws
                    code = self.parity_code
                    data = {"sessionToken": self.session_token}
                    if self.fault == "changed_token":
                        data["token"] = secrets.token_hex(24)
                else:
                    self.assertEqual(envelope.get("sessionToken"), self.session_token)
                    if url == "/v1/platform/device/machine-info":
                        data = {
                            "sn": self.identity,
                            "deviceCode": "JS001",
                            "firmware": {"version": "synthetic-firmware"},
                        }
                    elif url == "/v1/platform/device/state/sync":
                        self.assertEqual(envelope["method"], "POST")
                        self.assertEqual(envelope["data"], {})
                        data = {}
                        if self.report:
                            await ws.send_bytes(
                                encode_frame(
                                    json.dumps(FIXTURE["state_report"]).encode()
                                )
                            )
                    elif url == "/v1/platform/camera/list":
                        data = self.cameras
                    elif url == "/v1/platform/accessories/list":
                        data = self.slots
                    elif url == "/v1/platform/accessories/control":
                        self.assertEqual(
                            (envelope["params"], envelope["data"]),
                            ({"id": 71}, {"command": "M9098"}),
                        )
                        data = FIXTURE["connected_response"]
                    elif url == "/v1/platform/camera/snap":
                        self.assertEqual(envelope["method"], "POST")
                        self.assertEqual(envelope["params"], {"name": "far"})
                        data = {"filename": "synthetic-issued-snapshot.jpg"}
                    elif url == "/v1/filetransfer/download":
                        self.assertTrue(
                            self.file_open, "File socket must open before handshake"
                        )
                        self.assertEqual(
                            envelope["data"]["filename"],
                            "synthetic-issued-snapshot.jpg",
                        )
                        data = {
                            "filesize": len(self.image),
                            "digestdata": hashlib.md5(self.image).hexdigest(),
                            "digesttype": 1,
                            "packetsize": 8192,
                        }
                        if self.fault == "digest":
                            data["digestdata"] = "00" * 16
                        if self.fault == "oversized":
                            data["filesize"] = 24 * 1024 * 1024 + 1
                    elif url == "/v1/filetransfer/finish":
                        self.assertTrue(
                            self.file_open, "File socket must stay open through finish"
                        )
                        code, data = self.finish_code, {}
                    else:
                        raise AssertionError("Non-admitted request")
                response = {
                    "type": "response",
                    "transactionId": envelope["transactionId"],
                    "code": code,
                    "data": data,
                }
                if self.fault == "missing_code":
                    response.pop("code")
                if self.fault == "nested_auth":
                    response["data"] = {"code": 403, "data": {}}
                if not ws.closed:
                    await ws.send_bytes(encode_frame(json.dumps(response).encode()))
        return ws

    async def test_authenticated_reads_preserve_observed_states_and_empty_bt_inventory(
        self,
    ):
        state = await self.protocol.poll_readonly()
        self.assertEqual(
            state.raw_states,
            {"mode": "Idle", "moveState": "idle", "gohomeState": "finish"},
        )
        self.assertIsNone(state.status)
        self.assertEqual(state.bluetooth, ())
        self.assertTrue(state.modules[71].online)
        self.assertIsNone(state.modules[71].common_status)
        self.assertFalse(self.protocol.connected)
        self.assertEqual(self.protocol._session_token, "")
        self.assertTrue(all(client == CLIENT for _, client in self.connections))

    async def test_sync_empty_return_never_creates_state_and_offline_dongle_is_not_empty_bt(
        self,
    ):
        self.report = False
        self.slots[0]["status"] = False
        state = await self.protocol.poll_readonly()
        self.assertEqual(state.raw_states, {})
        self.assertIsNone(state.bluetooth)
        self.assertFalse(
            any(item["url"].endswith("/accessories/control") for item in self.requests)
        )

    async def test_refused_reconnect_latches_without_fallback_or_button_poll(self):
        for code in (406, 403, 503, 409):
            with self.subTest(code=code):
                self.protocol = X1ReadOnlyProtocol(self.binding)
                self.parity_code = code
                before = len(self.requests)
                for _ in range(2):
                    with self.assertRaises(X1AuthError):
                        await self.protocol.poll_readonly()
                self.assertEqual(len(self.requests) - before, 1)
                self.assertIsNone(self.protocol._session)

    async def test_nested_refusal_and_missing_codes_are_not_success(self):
        self.fault = "nested_auth"
        with self.assertRaises(X1AuthError):
            await self.protocol.poll_readonly()
        self.protocol = X1ReadOnlyProtocol(self.binding)
        self.fault = "missing_code"
        with self.assertRaises(X1ProtocolError):
            await self.protocol.poll_readonly()

    async def test_changed_permanent_token_is_refused_without_reconnect(self):
        self.fault = "changed_token"
        for _ in range(2):
            with self.assertRaises(X1AuthError):
                await self.protocol.poll_readonly()
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(self.protocol._session)

    async def test_pin_identity_redirect_and_timeout_fail_closed(self):
        self.protocol = X1ReadOnlyProtocol(replace(self.binding, leaf_sha256="00" * 32))
        with self.assertRaises(X1AuthError):
            await self.protocol.poll_readonly()
        self.assertEqual(self.requests, [])
        self.protocol = X1ReadOnlyProtocol(self.binding)
        self.redirect = True
        with self.assertRaises(X1ProtocolError):
            await self.protocol.poll_readonly()
        self.assertFalse(self.redirect_followed)
        self.redirect = False
        self.identity = "OTHER-SYNTHETIC"
        with self.assertRaises(X1AuthError):
            await self.protocol.poll_readonly()
        self.identity = SERIAL
        self.protocol = X1ReadOnlyProtocol(self.binding)
        self.delay = 0.1
        with patch(
            "custom_components.xtool.protocols.ws_v2.x1_transport._TIMEOUT", 0.03
        ):
            with self.assertRaises(TimeoutError):
                await self.protocol.poll_readonly()
        self.assertIsNone(self.protocol._session)

    async def test_snapshot_returns_verified_jpeg_only_after_finish_with_file_channel_open(
        self,
    ):
        image = await self.protocol.snapshot("far")
        self.assertEqual(image, self.image)
        self.assertFalse(self.protocol.connected)
        self.assertEqual(self.requests[-1]["url"], "/v1/filetransfer/finish")
        self.assertEqual(
            self.connections, [("instruction", CLIENT), ("file_stream", CLIENT)]
        )
        with self.assertRaises(X1ProtocolError):
            await self.protocol.snapshot("ldm")

    async def test_corruption_duplicates_offsets_sizes_and_digest_never_finish(self):
        for fault in ("crc", "duplicate", "offset", "channel", "digest", "oversized"):
            with self.subTest(fault=fault):
                self.fault = fault
                self.requests.clear()
                with self.assertRaises(X1ProtocolError):
                    await self.protocol.snapshot("far")
                self.assertFalse(
                    any(item["url"].endswith("/finish") for item in self.requests)
                )
                self.assertIsNone(self.protocol._session)

    async def test_file_socket_pin_failure_never_downloads_or_returns_an_image(self):
        self.file_pin_mismatch = True
        with self.assertRaises(X1AuthError) as caught:
            await self.protocol.snapshot("far")
        self.assertEqual(str(caught.exception), "X1 pinned certificate mismatch")
        self.assertFalse(
            any(item["url"].startswith("/v1/filetransfer/") for item in self.requests)
        )
        self.assertIsNone(self.protocol._session)

    async def test_finish_failure_is_preserved_without_image_cache_success(self):
        self.finish_code = -1
        with self.assertRaises(X1ProtocolError):
            await self.protocol.snapshot("far")
        self.finish_code = 0
        self.fault = "digest"
        with self.assertRaises(X1ProtocolError):
            await self.protocol.snapshot("far")

    async def test_digest_matched_truncated_jpeg_still_fails_before_finish(self):
        self.image = self.image[:-32]
        with self.assertRaises(X1ProtocolError):
            await self.protocol.snapshot("far")
        self.assertFalse(any(item["url"].endswith("/finish") for item in self.requests))

    async def test_real_ha_coordinator_entities_publish_unknown_unavailable_and_snapshots(
        self,
    ):
        from homeassistant.core import HomeAssistant
        from homeassistant.exceptions import HomeAssistantError
        from homeassistant.helpers import frame
        from homeassistant.helpers.entity_platform import EntityPlatform

        hass = HomeAssistant(self.temp.name)
        frame.async_setup(hass)
        coordinator = X1ReadOnlyCoordinator(
            hass,
            protocol=self.protocol,
            device_name="Synthetic X1",
            serial_number=SERIAL,
            firmware_version="",
            model=XTOOL_X1_DISCOVERY,
            enable_firmware_updates=True,
            power_switch_entity_id="switch.synthetic",
        )
        await coordinator.async_refresh()
        self.assertTrue(coordinator.last_update_success)
        self.assertFalse(coordinator.enable_firmware_updates)
        self.assertIsNone(coordinator.power_switch_entity_id)
        for factory in (
            "build_switches",
            "build_numbers",
            "build_buttons",
            "build_lights",
            "build_fans",
            "build_selects",
            "build_updates",
            "build_events",
        ):
            self.assertEqual(getattr(coordinator, factory)(), [], factory)
        sensors = coordinator.build_sensors()
        binaries = coordinator.build_binary_sensors()
        cameras = coordinator.build_cameras()
        self.assertEqual(
            {camera._camera_name for camera in cameras}, {"far", "near", "side"}
        )
        platform = EntityPlatform(
            hass=hass,
            logger=logging.getLogger(__name__),
            domain="sensor",
            platform_name="xtool",
            platform=None,
            scan_interval=timedelta(seconds=60),
            entity_namespace=None,
        )
        mode = next(
            sensor for sensor in sensors if getattr(sensor, "_key", None) == "mode"
        )
        mode.entity_id = "sensor.synthetic_x1_mode"
        mode.add_to_platform_start(hass, platform, None)
        await mode.add_to_platform_finish()
        self.assertEqual(hass.states.get(mode.entity_id).state, "Idle")
        count = next(
            sensor
            for sensor in sensors
            if sensor.name == "Connected Bluetooth accessories"
        )
        self.assertEqual(count.native_value, 0)
        dongle = next(entity for entity in binaries if entity._module_id == 71)
        offline = next(entity for entity in binaries if entity._module_id == 82)
        self.assertTrue(dongle.is_on)
        self.assertFalse(offline.is_on)
        far = next(camera for camera in cameras if camera._camera_name == "far")
        self.assertEqual(await far.async_camera_image(), self.image)
        self.report = False
        self.slots = []
        self.cameras = []
        await coordinator.async_refresh()
        self.assertEqual(hass.states.get(mode.entity_id).state, "unknown")
        self.assertIsNone(dongle.is_on)
        self.assertIsNone(count.native_value)
        self.assertFalse(far.available)
        with self.assertRaises(HomeAssistantError):
            await far.async_camera_image()
        self.parity_code = 406
        with self.assertLogs("custom_components.xtool.coordinator", level="ERROR"):
            await coordinator.async_refresh()
        self.assertEqual(hass.states.get(mode.entity_id).state, "unavailable")
        self.assertFalse(dongle.available)
        await mode.async_remove()
        await coordinator.async_shutdown()
