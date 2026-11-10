"""X1 discovery, model isolation and read-only entities with synthetic identity."""

import asyncio
import json
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

_COMPONENT = Path.cwd() / "custom_components/xtool"
for _name, _path in (
    ("custom_components.xtool", _COMPONENT),
    ("custom_components.xtool.protocols", _COMPONENT / "protocols"),
    ("custom_components.xtool.protocols.ws_v2", _COMPONENT / "protocols/ws_v2"),
):
    if _name not in sys.modules:
        _package = ModuleType(_name)
        _package.__path__ = [str(_path)]
        sys.modules[_name] = _package

from custom_components.xtool import discovery
from custom_components.xtool.protocols.base import XtoolDeviceState
from custom_components.xtool.protocols.ws_v2.coordinator_x1 import (
    X1DiscoveryCoordinator,
)
from custom_components.xtool.protocols.ws_v2.entities_x1 import X1DiscoveryReachable
from custom_components.xtool.protocols.ws_v2.models import (
    WSV2_MODELS,
    XTOOL_M2_WSV2,
    XTOOL_X1_DISCOVERY,
)
from custom_components.xtool.protocols.ws_v2.protocol_x1 import X1DiscoveryProtocol

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/x1_sanitized.json").read_text()
)
_HOST = "192.0.2.12"  # RFC 5737 documentation address, never a real target.


def recorded_device():
    return discovery._v2_parse_response(_FIXTURE["discovery_response"], 12345, _HOST)


class RecordedDiscoveryTests(unittest.TestCase):
    def test_recorded_encrypted_reply_preserves_exact_model_and_platform(self):
        raw = _FIXTURE["discovery_response"]
        packet = discovery._v2_encrypt(
            json.dumps(raw).encode(), discovery.V2_COMMON_KEY
        )
        decoded = discovery._v2_decrypt(packet, discovery.V2_COMMON_KEY)
        device = discovery._v2_parse_response(decoded, 12345, _HOST)
        self.assertEqual(device.device_code, "JS001")
        self.assertEqual(device.platform_version, "2.0")
        self.assertEqual(device.serial_number, "EXAMPLE-X1-NOT-REAL")
        self.assertIsNone(discovery._v2_parse_response(decoded, 12346, _HOST))

    def test_legacy_discovery_callers_keep_optional_metadata_defaults(self):
        old = discovery.DiscoveredDevice(ip=_HOST, name="legacy", version="example")
        self.assertEqual((old.device_code, old.platform_version), ("", ""))

    def test_genuine_x1_is_not_m2_and_has_no_unverified_capabilities(self):
        self.assertIn(XTOOL_X1_DISCOVERY, WSV2_MODELS)
        self.assertEqual(XTOOL_X1_DISCOVERY.model_id, "JS001")
        self.assertIs(XTOOL_X1_DISCOVERY.protocol_class, X1DiscoveryProtocol)
        self.assertIsNot(
            XTOOL_X1_DISCOVERY.protocol_class, XTOOL_M2_WSV2.protocol_class
        )
        self.assertTrue(XTOOL_X1_DISCOVERY.is_read_only)
        self.assertEqual(XTOOL_X1_DISCOVERY.camera_names, ())
        self.assertEqual(XTOOL_X1_DISCOVERY.firmware_content_id, "")
        self.assertFalse(
            any(
                value
                for key, value in vars(XTOOL_X1_DISCOVERY).items()
                if key.startswith("has_")
            )
        )
        self.assertEqual(XTOOL_M2_WSV2.firmware_machine_type, "JS002")


class X1ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_unicast_discovery_is_used_and_operational_status_is_unknown(
        self,
    ):
        protocol = X1DiscoveryProtocol(_HOST)
        with patch(
            "custom_components.xtool.protocols.ws_v2.protocol_x1._probe_v2",
            new_callable=AsyncMock,
            return_value=[recorded_device()],
        ) as probe:
            await protocol.connect()
            info = await protocol.get_device_info()
            self.assertEqual(await protocol.get_version(), "example-firmware")
            state = XtoolDeviceState()
            await protocol.poll_state(state)
        self.assertEqual(info.serial_number, "EXAMPLE-X1-NOT-REAL")
        self.assertEqual(info.device_name, "xTool_X1_example")
        self.assertTrue(state.available)
        self.assertIsNone(state.status)
        self.assertEqual(state.connected_accessories, {})
        self.assertEqual(info.laser_power_watts, 0)  # unknown, not an inferred head
        self.assertEqual(probe.await_count, 2)
        for call in probe.await_args_list:
            self.assertEqual(call.args, (_HOST,))
            self.assertEqual(
                call.kwargs,
                {"timeout": 5.0, "broadcast": False, "log_identifiers": False},
            )
        await protocol.disconnect()
        self.assertFalse(protocol.connected)

    async def test_wrong_model_platform_or_incomplete_identity_fails_closed(self):
        good = recorded_device()
        for changes in (
            {"device_code": "JS002"},
            {"device_code": ""},
            {"platform_version": "unknown"},
            {"serial_number": ""},
            {"serial_number": None},
            {"version": ""},
            {"name": 1},
            {"protocol_version": "V1"},
        ):
            with self.subTest(changes=changes):
                protocol = X1DiscoveryProtocol(_HOST)
                with patch(
                    "custom_components.xtool.protocols.ws_v2.protocol_x1._probe_v2",
                    new_callable=AsyncMock,
                    return_value=[replace(good, **changes)],
                ):
                    with self.assertRaises(ConnectionError):
                        await protocol.connect()
                self.assertFalse(protocol.connected)
                self.assertIsNone(protocol.discovery)

    async def test_missing_other_host_and_ambiguous_replies_are_not_success(self):
        for rows in (
            [],
            [replace(recorded_device(), ip="192.0.2.13")],
            [recorded_device(), recorded_device()],
        ):
            protocol = X1DiscoveryProtocol(_HOST)
            with patch(
                "custom_components.xtool.protocols.ws_v2.protocol_x1._probe_v2",
                new_callable=AsyncMock,
                return_value=rows,
            ):
                with self.assertRaises(ConnectionError):
                    await protocol.connect()
            self.assertFalse(protocol.connected)

    async def test_dhcp_reassignment_and_timeout_do_not_retain_live_identity(self):
        protocol = X1DiscoveryProtocol(_HOST)
        with patch(
            "custom_components.xtool.protocols.ws_v2.protocol_x1._probe_v2",
            new_callable=AsyncMock,
            return_value=[recorded_device()],
        ):
            await protocol.connect()
        with patch(
            "custom_components.xtool.protocols.ws_v2.protocol_x1._probe_v2",
            new_callable=AsyncMock,
            return_value=[
                replace(recorded_device(), serial_number="DIFFERENT-EXAMPLE")
            ],
        ):
            with self.assertRaises(ConnectionError):
                await protocol.refresh()
        self.assertFalse(protocol.connected)
        self.assertIsNone(protocol.discovery)
        with patch(
            "custom_components.xtool.protocols.ws_v2.protocol_x1._probe_v2",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError,
        ):
            with self.assertRaises(asyncio.TimeoutError):
                await protocol.refresh()
        self.assertFalse(protocol.connected)

    async def test_saved_identity_is_bound_before_first_network_read(self):
        protocol = X1DiscoveryProtocol(_HOST)
        protocol.bind_serial("SAVED-EXAMPLE")
        with patch(
            "custom_components.xtool.protocols.ws_v2.protocol_x1._probe_v2",
            new_callable=AsyncMock,
            return_value=[recorded_device()],
        ):
            with self.assertRaises(ConnectionError):
                await protocol.connect()
        self.assertFalse(protocol.connected)

    async def test_no_control_request_or_tls_surface(self):
        protocol = X1DiscoveryProtocol(_HOST)
        for name in (
            "request",
            "set_config",
            "parts_control",
            "passthrough",
            "camera_snap",
            "set_processing_state",
            "home",
            "move",
            "pair",
        ):
            self.assertFalse(hasattr(protocol, name), name)
        with self.assertRaises(NotImplementedError):
            await protocol.flash_firmware([], lambda _: None)

    def test_real_ha_import_and_existing_model_registry_still_load(self):
        code = """import custom_components.xtool
from custom_components.xtool.protocols import DEVICE_MODELS, detect_models, manual_model_options
from custom_components.xtool.protocols.ws_v2.protocol_x1 import X1DiscoveryProtocol
x1 = DEVICE_MODELS['JS001_V2']
assert x1.protocol_class is X1DiscoveryProtocol
assert detect_models('xTool_X1_example')[0] is x1
assert all(m.model_id != 'JS001' for m in detect_models('xTool_M2_example'))
assert 'JS001_V2' in manual_model_options()
assert DEVICE_MODELS['M2_V2'].firmware_machine_type == 'JS002'
print('REGISTRY_IMPORT_' + 'VERIFIED')
"""
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("REGISTRY_IMPORT_VERIFIED", result.stdout)


class X1CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_ha_coordinator_is_readonly_slow_and_binds_saved_identity(self):
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import frame
        from homeassistant.helpers.update_coordinator import UpdateFailed

        hass = HomeAssistant(str(Path.cwd()))
        frame.async_setup(hass)
        protocol = X1DiscoveryProtocol(_HOST)
        coordinator = X1DiscoveryCoordinator(
            hass,
            protocol=protocol,
            device_name="example",
            serial_number="EXAMPLE-X1-NOT-REAL",
            firmware_version="",
            model=XTOOL_X1_DISCOVERY,
            enable_firmware_updates=True,
            scan_interval=1,
        )
        self.assertFalse(coordinator.enable_firmware_updates)
        self.assertEqual(coordinator.update_interval.total_seconds(), 60)
        self.assertNotIn(coordinator.serial_number, coordinator.name)
        with patch(
            "custom_components.xtool.protocols.ws_v2.protocol_x1._probe_v2",
            new_callable=AsyncMock,
            return_value=[recorded_device()],
        ):
            await coordinator.async_refresh()
        self.assertTrue(coordinator.last_update_success)
        self.assertTrue(coordinator.data.available)
        self.assertIsNone(coordinator.data.status)
        self.assertEqual(coordinator.firmware_version, "example-firmware")
        with patch(
            "custom_components.xtool.protocols.ws_v2.protocol_x1._probe_v2",
            new_callable=AsyncMock,
            return_value=[],
        ):
            with self.assertLogs(
                "custom_components.xtool.coordinator", level="ERROR"
            ) as logs:
                await coordinator.async_refresh()
            with self.assertRaises(UpdateFailed):
                await coordinator._async_update_data()
        self.assertFalse(coordinator.last_update_success)
        self.assertIsNone(protocol.discovery)
        self.assertNotIn(coordinator.serial_number, "".join(logs.output))
        await coordinator.async_shutdown()


class X1EntitySurfaceTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = object.__new__(X1DiscoveryCoordinator)
        self.coordinator.protocol = X1DiscoveryProtocol(_HOST)
        self.coordinator.protocol._discovery = recorded_device()
        self.coordinator.model = XTOOL_X1_DISCOVERY
        self.coordinator.serial_number = "EXAMPLE-X1-NOT-REAL"
        self.coordinator.device_name = "xTool_X1_example"
        self.coordinator.firmware_version = "example-firmware"
        self.coordinator.mac_address = ""
        self.coordinator._hardware_version = ""
        self.coordinator.power_switch_entity_id = None
        self.coordinator.data = XtoolDeviceState(available=True)
        self.coordinator.last_update_success = True

    def test_only_observed_identity_and_discovery_reachability_are_exposed(self):
        sensors = self.coordinator.build_sensors()
        self.assertEqual(
            {sensor.native_value for sensor in sensors},
            {"xTool_X1_example", "example-firmware", "JS001", "2.0"},
        )
        self.assertEqual(len(sensors), 4)
        for factory in (
            "build_cameras",
            "build_events",
            "build_fans",
            "build_lights",
            "build_switches",
            "build_numbers",
            "build_buttons",
            "build_selects",
            "build_updates",
        ):
            self.assertEqual(getattr(self.coordinator, factory)(), [], factory)
        (reachable,) = self.coordinator.build_binary_sensors()
        self.assertIsInstance(reachable, X1DiscoveryReachable)
        self.assertTrue(reachable.is_on)
        self.coordinator.last_update_success = False
        self.assertFalse(reachable.is_on)
        self.assertTrue(reachable.available)
        self.assertFalse(sensors[0].available)


if __name__ == "__main__":
    unittest.main()
