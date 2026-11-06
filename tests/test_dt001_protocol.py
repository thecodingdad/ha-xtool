"""DT001 regressions against captured 40.100.028.00.ht6 responses.

Run without Home Assistant: python -m unittest discover -s tests -p test_dt001_protocol.py
Only the unused HA TLS helper is substituted; the protocol and state are real.
"""
import importlib.util
import importlib
import ast
import base64
import copy
import json
import logging
from pathlib import Path
import ssl
import sys
from types import ModuleType
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "_dt001_regression"
for suffix, directory in (
    ("", "custom_components/xtool"),
    (".protocols", "custom_components/xtool/protocols"),
    (".protocols.ws_v2", "custom_components/xtool/protocols/ws_v2"),
):
    package = ModuleType(PREFIX + suffix)
    package.__path__ = [str(ROOT / directory)]
    sys.modules[package.__name__] = package


def load(suffix, path):
    name = PREFIX + suffix
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load(".const", "custom_components/xtool/const.py")
base = load(".protocols.base", "custom_components/xtool/protocols/base.py")
try:
    import homeassistant.util.ssl
except ImportError:
    tls_stub = ModuleType("homeassistant.util.ssl")
    tls_stub.get_default_no_verify_context = ssl._create_unverified_context
    tls_modules = {"homeassistant.util.ssl": tls_stub}
else:
    tls_modules = {}
with patch.dict(sys.modules, tls_modules):
    protocol = load(".protocols.ws_v2.protocol", "custom_components/xtool/protocols/ws_v2/protocol.py")
    dt001 = load(".protocols.ws_v2.protocol_dt001", "custom_components/xtool/protocols/ws_v2/protocol_dt001.py")
FIXTURE = json.loads((ROOT / "tests/fixtures/dt001_40_100_028.json").read_text())
accessories = importlib.import_module(PREFIX + ".protocols.accessories")


def coordinator_core():
    """Load the real accessory walk independently of HA's scheduler."""
    tree = ast.parse((ROOT / "custom_components/xtool/coordinator.py").read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if (
        isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_TE_TYPE_ID_BY_NUMERIC"
        or isinstance(node, ast.FunctionDef) and node.name == "_resolve_type_id"
    )]
    coordinator_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "XtoolCoordinator")
    method = next(node for node in coordinator_class.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_poll_accessories")
    nodes.append(ast.ClassDef(name="CoordinatorHarness", bases=[], keywords=[], body=[method], decorator_list=[]))
    scope = dict(__package__=PREFIX, Any=Any, asyncio=__import__("asyncio"),
                 XtoolDeviceState=base.XtoolDeviceState, AccessoryState=base.AccessoryState,
                 _LOGGER=logging.getLogger(__name__))
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "coordinator.py", "exec"), scope)
    return scope


class DT001Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.device = dt001.DT001WSV2Protocol("192.0.2.1")
        self.state = base.XtoolDeviceState()

    async def test_cpu_temperature_is_degrees_on_dt001(self):
        self.device.request = AsyncMock(return_value=FIXTURE[self.device.PATH_RUNTIME_INFOS])
        await self.device._poll_runtime_status(self.state)
        self.assertEqual(self.state.cpu_temp, 38)

    def test_real_snapshot_updates_oven_and_consumables(self):
        for event in FIXTURE["events"]:
            self.device._dispatch_push(event)
        self.device._apply_latest_to_state(self.state)
        self.assertTrue(self.state.heater_connected)
        self.assertGreater(self.state.heater_temperature, 0)
        self.assertEqual(self.state.ink_white, "normal")
        self.assertFalse(self.state.waste_tank_full)
        self.assertEqual(self.state.ambient_humidity, 46)

    def test_full_waste_tank_clears_after_emptying(self):
        for value, full in (("full", True), ("normal", False)):
            self.device._apply_water_bottle(self.state, {"waste_water_bottle": value})
            self.assertIs(self.state.waste_tank_full, full)
            self.assertEqual(self.state.waste_water, value)

    def test_full_waste_push_uses_snapshot_not_alarm_history(self):
        event = {
            "url": "/device/status", "data": {
                "module": "DEVICE_STATUS", "type": "INFO",
                "info": {"waterBottle": {"waste_water_bottle": "full"}},
            },
        }
        self.device._dispatch_push(event)
        self.device._apply_latest_to_state(self.state)
        self.assertTrue(self.state.waste_tank_full)
        event["data"]["info"]["waterBottle"]["waste_water_bottle"] = "normal"
        self.device._dispatch_push(event)
        self.device._apply_latest_to_state(self.state)
        self.assertFalse(self.state.waste_tank_full)

    def test_unknown_waste_state_is_not_assumed_safe(self):
        self.device._apply_water_bottle(self.state, {})
        self.assertIsNone(self.state.waste_tank_full)

    def test_unknown_consumable_enums_clear_previous_values(self):
        self.device._apply_water_bottle(self.state, {"waste_water_bottle": "full", "clean_water_bottle": "normal"})
        self.device._apply_water_bottle(self.state, {"waste_water_bottle": "future-state", "clean_water_bottle": "future-state"})
        self.device._apply_ink_bottle(self.state, {"ink_w": "normal"})
        self.device._apply_ink_bottle(self.state, {"ink_w": "future-state"})
        self.device._apply_latest_to_state(self.state)
        self.assertIsNone(self.state.waste_water)
        self.assertIsNone(self.state.waste_tank_full)
        self.assertIsNone(self.state.clean_water)
        self.assertIsNone(self.state.ink_white)

    def test_explicit_invalid_consumable_values_clear_but_missing_fields_preserve(self):
        for invalid in (None, "", False, 0, [], {}):
            with self.subTest(value=invalid):
                self.device._apply_water_bottle(self.state, {"waste_water_bottle": "full", "clean_water_bottle": "normal"})
                self.device._apply_ink_bottle(self.state, {"ink_w": "normal"})
                self.device._apply_water_bottle(self.state, {})
                self.device._apply_ink_bottle(self.state, {})
                self.assertTrue(self.state.waste_tank_full)
                self.assertEqual(self.state.ink_white, "normal")
                self.device._apply_water_bottle(self.state, {"waste_water_bottle": invalid, "clean_water_bottle": invalid})
                self.device._apply_ink_bottle(self.state, {"ink_w": invalid})
                self.assertIsNone(self.state.waste_tank_full)
                self.assertIsNone(self.state.waste_water)
                self.assertIsNone(self.state.clean_water)
                self.assertIsNone(self.state.ink_white)

    def test_removed_tank_invalidates_cached_full_until_reinstalled(self):
        for value, expected in (("full", True), ("detach", None), ("normal", False)):
            self.device._apply_water_bottle(self.state, {"waste_water_bottle": value})
            self.device._apply_latest_to_state(self.state)
            self.assertEqual(self.state.waste_water, value)
            self.assertIs(self.state.waste_tank_full, expected)

    def test_replay_physical_waste_tank_capture(self):
        expected = {"full": True, "normal": False, "detach": None}
        for observation in FIXTURE["waste_tank_physical_transitions"]:
            status = observation["waste_water"]
            self.device._apply_water_bottle(self.state, {"waste_water_bottle": status})
            self.device._apply_latest_to_state(self.state)
            self.assertEqual(self.state.waste_water, status)
            self.assertIs(self.state.waste_tank_full, expected[status])

    async def test_replay_physical_panel_polling_independently(self):
        for observation in FIXTURE["panel_physical_transitions"]:
            payload = observation["data"]
            self.device.request = AsyncMock(return_value=payload)
            await self.device._poll_runtime_status(self.state)
            self.device._apply_latest_to_state(self.state)
            expected = [bool(value) for value in payload["gapState"][:3]]
            self.assertEqual([
                self.state.top_cover_open, self.state.left_panel_open, self.state.right_panel_open,
            ], expected)

    def test_replay_physical_panel_pushes_with_empty_url(self):
        self.device._apply_panel_states(self.state, {"gapState": [0, 0, 0, 0]})
        expected = {"top_cover_open": False, "left_panel_open": False, "right_panel_open": False}
        fields = {"gap_top": "top_cover_open", "gap_left": "left_panel_open", "gap_right": "right_panel_open"}
        for record in FIXTURE["panel_push_events"]:
            event = record["event"]
            self.assertEqual(event["url"], "")
            data = event["data"]
            expected[fields[data["info"]]] = data["type"] == "OPEN"
            self.device._dispatch_push(event)
            self.device._apply_latest_to_state(self.state)
            for attr, opened in expected.items():
                self.assertIs(getattr(self.state, attr), opened)

    def test_invalid_or_missing_panel_data_is_not_assumed_closed(self):
        self.device._apply_panel_states(self.state, {})
        self.assertIsNone(self.state.top_cover_open)
        self.device._apply_panel_states(self.state, {"gapState": [1, 0, 0, 0]})
        self.assertTrue(self.state.top_cover_open)
        self.device._apply_panel_states(self.state, {"gapState": [None, 0, 0, 0]})
        self.assertIsNone(self.state.top_cover_open)

    def test_maintenance_values_and_reminder_thresholds(self):
        self.device._apply_statistics(FIXTURE[self.device.PATH_STATISTICS])
        self.device._apply_maintenance(FIXTURE["/v1/device/configs"])
        self.device._apply_latest_to_state(self.state)
        self.assertEqual(self.state.session_count, 61)
        self.assertEqual(self.state.printer_maintenance["cleanNozzleDeep"], 10)
        self.assertEqual(self.state.printer_maintenance["numKnifeCut"], 97)
        self.assertTrue(self.state.wiping_maintenance_due)
        self.assertFalse(self.state.fitting_replacement_due)
        self.device._apply_maintenance({"worktimeFromLastCleanMatain": 0})
        self.assertFalse(self.device._latest["wiping_maintenance_due"])

    def test_disabled_reminder_and_missing_data(self):
        self.device._apply_maintenance({})
        self.assertNotIn("wiping_maintenance_due", self.device._latest)
        self.device._apply_maintenance({"cleanRemindDay": -1})
        self.assertFalse(self.device._latest["wiping_maintenance_due"])

    async def test_other_models_keep_millidegree_conversion(self):
        other = protocol.WSV2Protocol("192.0.2.1")
        other.request = AsyncMock(return_value={"cpuTemp": 43565})
        await other._poll_runtime_status(self.state)
        self.assertEqual(self.state.cpu_temp, 43)
        self.assertNotEqual(self.device.PATH_STATISTICS, protocol.WSV2Protocol.PATH_STATISTICS)

    def test_other_models_keep_modern_and_legacy_statistics(self):
        for payload, expected in (
            ({"timeModeWorking": 120, "timeSystemWork": 600, "numOnlineWorking": 4, "numOfflineWorking": 5}, (120, 480, 9)),
            ({"workingTime": 15, "standbyTime": 30, "sessionCount": 2}, (15, 30, 2)),
            ({"totalWorkTime": 20, "totalStandbyTime": 40, "workCount": 3}, (20, 40, 3)),
        ):
            with self.subTest(payload=payload):
                other = protocol.WSV2Protocol("192.0.2.1")
                state = base.XtoolDeviceState()
                other._apply_statistics(payload)
                other._apply_latest_to_state(state)
                self.assertEqual((state.working_seconds, state.standby_seconds, state.session_count), expected)

    async def test_dt001_modes_do_not_change_shared_laser_mapping(self):
        for mode, submode, expected in (
            ("P_CLEAN", "", "cleaning"),
            ("P_CALIBRATIPON", "", "calibrating"),
            ("Work", "workReady", "processing_ready"),
            ("Work", "working", "processing"),
            ("Work", "workPause", "paused"),
            ("P_IDLE", "", "idle"),
        ):
            info = {"mode": mode, "subMode": submode}
            self.device.request = AsyncMock(return_value={"curMode": info})
            await self.device._poll_runtime_status(self.state)
            self.assertEqual(self.state.status, expected)
            self.device._latest.clear()
            self.device._dispatch_push({"url": "/work/mode", "data": {
                "module": "STATUS_CONTROLLER", "type": "MODE_CHANGE", "info": info,
            }})
            self.device._apply_latest_to_state(self.state)
            self.assertEqual(self.state.status, expected)
        self.assertEqual(protocol.WSV2_MODE_MAP["P_WORK"], "idle")
        self.assertNotIn("WORK", protocol.WSV2_MODE_MAP)

    async def test_oven_continues_after_printer_returns_idle(self):
        for mode, flag, powder, expected in (
            ("Work", 262145, True, ("processing", True, False)),
            ("P_IDLE", 262144, False, ("idle", True, False)),
            ("P_IDLE", 0, False, ("idle", False, False)),
            ("P_IDLE", 32768, False, ("idle", False, True)),
        ):
            self.device.request = AsyncMock(return_value={
                "curMode": {"mode": mode, "subMode": "working" if mode == "Work" else ""},
                "deviceBusyState": {"busyFlag": flag}, "powderLoopRun": powder,
            })
            await self.device._poll_runtime_status(self.state)
            self.device._apply_latest_to_state(self.state)
            self.assertEqual((self.state.status, self.state.oven_processing, self.state.oven_feed_active), expected)
            self.assertIs(self.state.powder_loop_running, powder)

    async def test_replay_physical_cleaning_and_complete_oven_cycle(self):
        for record in FIXTURE["oven_and_cleaning_physical_transitions"]:
            with self.subTest(utc=record["observed_utc"]):
                self.device.request = AsyncMock(return_value=record["runtime"])
                await self.device._poll_runtime_status(self.state)
                self.device._apply_latest_to_state(self.state)
                for key, expected in record["expected"].items():
                    self.assertEqual(getattr(self.state, key), expected)
                self.assertIs(self.state.film_position_ready, record["runtime"]["filmPosStatus"])
                self.assertIs(self.state.powder_loop_running, record["runtime"]["powderLoopRun"])

    def test_replay_captured_print_elapsed_reports(self):
        for event in FIXTURE["print_elapsed_pushes"]:
            self.device._dispatch_push(event)
            self.device._apply_latest_to_state(self.state)
            self.assertEqual(self.state.task_time, event["data"]["info"]["processingTime"])

    def test_snapshot_tracks_oven_independently_of_printer_and_heater(self):
        for flag, expected in ((262145, True), (262144, True), (0, False)):
            self.device._dispatch_push({"url": "/device/status", "data": {
                "module": "DEVICE_STATUS", "type": "INFO", "info": {
                    "curMode": {"mode": "P_IDLE", "subMode": ""},
                    "deviceBusyState": {"busyFlag": flag},
                    "heater": {"connectState": "Connected", "heaterTemp": 110, "heatingStatus": 1},
                },
            }})
            self.device._apply_latest_to_state(self.state)
            self.assertEqual(self.state.status, "idle")
            self.assertIs(self.state.oven_processing, expected)
            self.assertEqual(self.state.heater_temperature, 110)

    def test_print_elapsed_report_and_result_are_seconds(self):
        self.device._dispatch_push({"url": "/print_task/status", "data": {
            "module": "PRINT_TASK", "type": "TASK_INFO_REPORT",
            "info": {"taskId": "example-job", "processingTime": 57, "currentPrintPass": 54, "totalPrintPass": 106},
        }})
        self.device._apply_latest_to_state(self.state)
        self.assertEqual(self.state.task_time, 57)
        self.assertEqual(self.state.task_id, "example-job")
        self.device._dispatch_push({"url": "/work/result", "data": {
            "module": "WORK_RESULT", "type": "WORK_FINISHED",
            "info": {"result": "success", "taskId": "example-job", "timeUse": 105},
        }})
        self.device._apply_latest_to_state(self.state)
        self.assertEqual(self.state.task_time, 105)
        self.assertEqual(self.state.last_job_time_seconds, 105)

    async def test_optional_endpoint_rejection_cached_but_transient_error_retried(self):
        self.device.request = AsyncMock(side_effect=RuntimeError("Device error code 404: missing"))
        for _ in range(2):
            self.assertIsNone(await self.device._poll_optional_endpoint(self.device.PATH_ROASTER))
        self.device.request.assert_awaited_once()
        self.device.request = AsyncMock(side_effect=[TimeoutError(), {"waste_water_bottle": "normal"}])
        self.assertIsNone(await self.device._poll_optional_endpoint(self.device.PATH_WATER_BOTTLE))
        self.assertEqual(await self.device._poll_optional_endpoint(self.device.PATH_WATER_BOTTLE), {"waste_water_bottle": "normal"})
        self.assertEqual(self.device.request.await_count, 2)

    async def test_missing_oven_does_not_break_printer_or_consumables(self):
        async def reply(route, method="GET", **kwargs):
            if route == self.device.PATH_ROASTER:
                raise RuntimeError("Device error code 404: missing")
            return copy.deepcopy(FIXTURE.get(route, {}))

        self.device.request = AsyncMock(side_effect=reply)
        self.device._connected = True
        await self.device.poll_state(self.state)
        self.assertEqual(self.state.ink_white, "normal")
        self.assertFalse(self.state.waste_tank_full)
        self.assertEqual(self.state.cpu_temp, 38)
        self.assertIsNone(self.state.heater_temperature)

    def test_disconnected_oven_clears_stale_temperature_and_activity(self):
        self.device._apply_roaster({"connectState": "Connected", "heaterTemp": 110, "heatingStatus": 1})
        self.device._apply_busy_state({"deviceBusyState": {"busyFlag": 262144}})
        self.device._apply_roaster({"connectState": "Disconnected", "heaterTemp": 0, "heatingStatus": 0})
        self.device._apply_latest_to_state(self.state)
        self.assertFalse(self.state.heater_connected)
        for attr in ("heater_temperature", "heating_status", "oven_processing", "oven_feed_active"):
            self.assertIsNone(getattr(self.state, attr))

    async def test_disconnected_oven_full_poll_invalidates_activity_in_state(self):
        async def reply(route, method="GET", **kwargs):
            if route == self.device.PATH_ROASTER:
                return {"connectState": "Disconnected", "heaterTemp": 0, "heatingStatus": 0}
            return copy.deepcopy(FIXTURE.get(route, {}))

        self.device.request = AsyncMock(side_effect=reply)
        self.device._connected = True
        await self.device.poll_state(self.state)
        self.assertFalse(self.state.heater_connected)
        self.assertIsNone(self.state.heater_temperature)
        self.assertIsNone(self.state.oven_processing)
        self.assertEqual(self.state.waste_water, "normal")

    def test_unknown_fields_and_malformed_optional_values(self):
        self.device._apply_maintenance({"cleanRemindDay": float("nan"), "numKnifeCut": True})
        self.assertEqual(self.device._latest["printer_maintenance"], {})
        self.device._apply_roaster({"heaterTemp": float("inf"), "heatingStatus": False})
        self.assertNotIn("heater_temperature", self.device._latest)
        self.device._dispatch_push({"url": "/device/status", "data": {
            "module": "DEVICE_STATUS", "type": "INFO", "info": {
                "humidifier": {"futureStatus": "unknown"}, "inkBottle": {"ink_w": "normal"},
                "waterBottle": None, "deviceBusyState": {"busyFlag": "unknown"},
            },
        }})
        self.device._apply_latest_to_state(self.state)
        self.assertEqual(self.state.ink_white, "normal")
        self.assertIsNone(self.state.oven_processing)

    def test_nozzle_clean_does_not_reset_manual_wiping_maintenance(self):
        self.device._apply_maintenance({"cleanRemindDay": 3, "worktimeFromLastCleanMatain": 300000, "cleanNozzleDeep": 10})
        self.device._apply_statistics({"cleanNozzleDeep": 11, "worktimeFromLastCleanMatain": 300300, "cyclePumpRunTime": 18736})
        self.device._apply_latest_to_state(self.state)
        self.assertEqual(self.state.printer_maintenance["cleanNozzleDeep"], 11)
        self.assertEqual(self.state.printer_maintenance["worktimeFromLastCleanMatain"], 300300)
        self.assertTrue(self.state.wiping_maintenance_due)
        self.assertNotIn("cyclePumpRunTime", self.state.printer_maintenance)

    def test_collision_push_reaches_declared_error_event_even_with_empty_url(self):
        # Source-derived example, not a captured physical collision. CLOSE is
        # Studio's HEAD_COLLISION_CLOSE fault, despite sounding like a clear.
        for url in ("", "/unexpected/firmware/route"):
            self.device._pending_events.clear()
            self.device._dispatch_push({"url": url, "data": {
                "module": "HEAD_COLLISION", "type": "CLOSE", "level": "error", "info": "2",
            }})
            self.assertEqual(self.device._pending_events, [(
                "error", "print_head_collision", {"raw_type": "CLOSE", "info": "2"},
            )])
        tree = ast.parse((ROOT / "custom_components/xtool/protocols/ws_v2/entities.py").read_text(encoding="utf-8"))
        declared = next(node for node in tree.body if isinstance(node, ast.AnnAssign)
                        and getattr(node.target, "id", "") == "WSV2_ERROR_EVENT_TYPES")
        self.assertIn("print_head_collision", ast.literal_eval(declared.value))

    async def test_sticky_collision_history_does_not_emit_a_new_fault(self):
        for payload in (
            FIXTURE["panel_physical_transitions"][-1]["data"],
            FIXTURE["collision_simulation_recovery"]["runtime"],
        ):
            self.assertIn("HEAD_COLLISION", json.dumps(payload))
            self.device.request = AsyncMock(return_value=payload)
            await self.device._poll_runtime_status(self.state)
            self.assertEqual(self.device._pending_events, [])
            self.assertNotIn("alarm_present", self.device._latest)

    def test_collision_count_is_reported_without_synthesizing_alarm_events(self):
        self.device._apply_statistics(FIXTURE[self.device.PATH_STATISTICS])
        self.device._apply_latest_to_state(self.state)
        self.assertEqual(self.state.printer_maintenance["bumpExceptions"], 33)
        self.device._apply_statistics({"bumpExceptions": FIXTURE["collision_simulation_recovery"]["after_clear_count"]})
        self.device._apply_latest_to_state(self.state)
        self.assertEqual(self.state.printer_maintenance["bumpExceptions"], 34)
        self.assertEqual(self.device._pending_events, [])

    def test_other_collision_types_or_unrelated_push_do_not_emit_fault(self):
        for data in (
            {"module": "HEAD_COLLISION", "type": "OPEN"},
            {"module": "HEAD_COLLISION", "type": "UNKNOWN"},
            {"module": "GAP", "type": "CLOSE", "info": "gap_top"},
            None,
        ):
            self.device._dispatch_push({"url": "", "data": data})
        self.assertEqual(self.device._pending_events, [])

    async def test_poll_initializes_oven_and_maintenance_without_writes(self):
        async def reply(route, method="GET", **kwargs):
            self.assertEqual(method, "GET")
            return FIXTURE.get(route, {})

        self.device.request = AsyncMock(side_effect=reply)
        self.device._connected = True
        await self.device.poll_state(self.state)
        self.assertTrue(self.state.heater_connected)
        self.assertEqual(self.state.heater_temperature, 84)
        self.assertEqual(self.state.cpu_temp, 38)
        self.assertTrue(self.state.wiping_maintenance_due)
        self.assertFalse(self.state.waste_tank_full)

    async def test_ap2_discovery_and_filter_poll_use_link_motion(self):
        ap2 = FIXTURE["ap2"]

        async def reply(route, method, *, data, **kwargs):
            self.assertEqual((route, method), ("/v1/parts/control", "POST"))
            self.assertEqual(data["link"], "linkMotion")
            packet = base64.b64decode(data["data_b64"])
            command = packet[6:-2].decode().strip()
            prefix = packet[1:6]
            if command == "M9098":
                text = ap2["connected_list"]
            elif command == "M9033":
                self.assertEqual(prefix, bytes([69, 115, 96, 1, 0]))
                text = ap2["status"]
            else:
                self.fail(f"Unexpected command {command}")
            return {"data_b64": accessories.encode_f0f7(text, prefix)}

        self.device.request = AsyncMock(side_effect=reply)
        scope = coordinator_core()
        coordinator = SimpleNamespace(protocol=self.device, model=SimpleNamespace(has_bt_accessories=True, model_id="DT001"))
        await scope["CoordinatorHarness"]._poll_accessories(coordinator, self.state)
        self.assertEqual(len(self.state.connected_accessories), 1)
        ap2_state = next(iter(self.state.connected_accessories.values()))
        self.assertEqual(ap2_state.type_id, "LargePurifier")
        self.assertEqual([ap2_state.fields[k] for k in (
            "filter_pre", "filter_medium", "filter_carbon", "filter_dense_carbon", "filter_hepa",
        )], [77, 89, 97, 93, 93])
        self.assertEqual(ap2_state.fields["mode"], "auto")

    async def test_paired_but_disconnected_ap2_is_not_polled_or_registered(self):
        self.device.parts_control = AsyncMock(return_value=FIXTURE["ap2"]["disconnected_list"])
        coordinator = SimpleNamespace(protocol=self.device, model=SimpleNamespace(has_bt_accessories=True, model_id="DT001"))
        self.state.connected_accessories = {"LargePurifier:example": base.AccessoryState(
            type_id="LargePurifier", sn="example", fields={"mode": "auto"},
        )}
        await coordinator_core()["CoordinatorHarness"]._poll_accessories(coordinator, self.state)
        self.assertEqual(self.state.connected_accessories, {})
        self.device.parts_control.assert_awaited_once()

    async def test_ap2_status_timeout_recovers_without_changing_identity(self):
        ap2 = FIXTURE["ap2"]
        walk = coordinator_core()["CoordinatorHarness"]._poll_accessories
        coordinator = SimpleNamespace(protocol=self.device, model=SimpleNamespace(has_bt_accessories=True, model_id="DT001"))
        self.device.parts_control = AsyncMock(side_effect=[
            ap2["connected_list"], ap2["status"].removeprefix("M9033 "),
            ap2["connected_list"], TimeoutError("simulated accessory read timeout"),
            ap2["connected_list"], ap2["status"].removeprefix("M9033 "),
        ])
        await walk(coordinator, self.state)
        original_keys = set(self.state.connected_accessories)
        self.assertEqual(len(original_keys), 1)
        await walk(coordinator, self.state)
        self.assertEqual(self.state.connected_accessories, {})
        await walk(coordinator, self.state)
        self.assertEqual(set(self.state.connected_accessories), original_keys)
        self.assertEqual(next(iter(self.state.connected_accessories.values())).fields["mode"], "auto")

    async def test_ap2_missing_serial_does_not_create_a_bluetooth_address_device(self):
        self.device.parts_control = AsyncMock(side_effect=[
            FIXTURE["ap2"]["connected_list"], "V40.212.007.4528.01 B03 C0 H76 I88 J96 K92 L92",
        ])
        coordinator = SimpleNamespace(protocol=self.device, model=SimpleNamespace(has_bt_accessories=True, model_id="DT001"))
        await coordinator_core()["CoordinatorHarness"]._poll_accessories(coordinator, self.state)
        self.assertEqual(self.state.connected_accessories, {})

    def test_ap2_auto_and_powered_off_modes(self):
        definition = accessories.get_definition("LargePurifier")
        text = FIXTURE["ap2"]["status"].removeprefix("M9033 ")
        for token, expected in (("C2", "auto"), ("C0", "auto"), ("A0", "off"), ("A2", "manual")):
            self.assertEqual(definition.parse_info(text.replace("C2", token))["mode"], expected)

    def test_replay_oven_loss_of_power_and_reconnection(self):
        for record in FIXTURE["oven_power_transitions"]:
            self.device._apply_roaster(record["roaster"])
            self.device._apply_latest_to_state(self.state)
            if record["roaster"]["connectState"] == "Disconnected":
                self.assertFalse(self.state.heater_connected)
                self.assertIsNone(self.state.heater_temperature)
            else:
                self.assertTrue(self.state.heater_connected)
                self.assertEqual(self.state.heater_temperature, record["roaster"]["heaterTemp"])

    async def test_other_models_keep_uart485_accessory_link(self):
        other = protocol.WSV2Protocol("192.0.2.1")
        other.request = AsyncMock(return_value={})
        await other.parts_control("M9098", bytes([71, 115, 100, 1, 0]))
        self.assertEqual(other.request.call_args.kwargs["data"]["link"], "uart485")

    def test_dt001_ap2_registration_is_status_only(self):
        tree = ast.parse((ROOT / "custom_components/xtool/protocols/accessories/entities.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_accessory_entities")
        scope = dict(Any=Any, XtoolCoordinator=Any, AccessoryState=base.AccessoryState,
                     get_definition=accessories.get_definition,
                     _PLATFORM_TO_CLASS={p: (lambda platform: lambda *args: platform)(p) for p in (
                         "sensor", "binary_sensor", "select", "switch", "number", "button",
                     )}, _AccessoryUpdate=lambda *args: "update")
        exec(compile(ast.Module(body=[function], type_ignores=[]), "entities.py", "exec"), scope)
        definition = accessories.get_definition("LargePurifier")
        fields = definition.parse_info(FIXTURE["ap2"]["status"].removeprefix("M9033 "))
        accessory = base.AccessoryState(type_id="LargePurifier", sn="test", fields=fields)
        entities = scope["build_accessory_entities"](
            SimpleNamespace(model=SimpleNamespace(model_id="DT001")), accessory,
        )
        self.assertGreater(len(entities), 5)
        self.assertEqual(set(entities), {"sensor"})


if __name__ == "__main__":
    unittest.main()
