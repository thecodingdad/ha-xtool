"""The xTool S1, as the maintainer's S1 behaves on hardware.

Built from what a real S1 answered (see s1_capture.py). The S1 shares the
base coordinator, the entity base classes, the accessory framework and
the platform setup with every other model: when one of these tests fails
after a change made for another model, that change reached the S1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.const import DOMAIN, XtoolStatus
from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.base import XtoolDeviceState
from custom_components.xtool.protocols.s1.protocol import (
    S1Protocol,
    get_laser_type_name,
    parse_accessories,
    parse_m2003,
)

from .s1_capture import M2003, SERIAL, FakeS1

POWER_SWITCH = "switch.laser_plug"
FIRMWARE = "V40.32.015.2025.01 B10"

# What the S1 creates, as (platform, key): the 44 entities the
# maintainer's S1 shows in Home Assistant, with a Power switch linked and
# an Air Pump (slot 2) and a Fire Extinguisher v1.5 (slot 4) connected.
EXPECTED_ENTITIES = {
    ("binary_sensor", "accessory_airpump_slot2_connected"),
    ("binary_sensor", "accessory_airpump_slot2_running"),
    ("binary_sensor", "alarm"),
    ("binary_sensor", "xcs_compat"),
    ("button", "cancel_job"),
    ("button", "home_all"),
    ("button", "home_xy"),
    ("button", "home_z"),
    ("button", "pause_job"),
    ("button", "resume_job"),
    ("event", "error"),
    ("event", "fire_warning"),
    ("event", "job"),
    ("light", "fill_light"),
    ("number", "accessory_airpump_slot2_close_delay"),
    ("number", "auto_power_off"),
    ("number", "smoking_fan_duration"),
    ("select", "flame_alarm_sensitivity"),
    ("sensor", "accessory_airpump_slot2_gear"),
    ("sensor", "connection_count"),
    ("sensor", "fire_level"),
    ("sensor", "ip_address"),
    ("sensor", "laser_module"),
    ("sensor", "laser_position_x"),
    ("sensor", "laser_position_y"),
    ("sensor", "laser_position_z"),
    ("sensor", "laser_power"),
    ("sensor", "riser_base"),
    ("sensor", "sd_card"),
    ("sensor", "session_count"),
    ("sensor", "standby_time"),
    ("sensor", "status"),
    ("sensor", "task_id"),
    ("sensor", "task_time"),
    ("sensor", "tool_runtime"),
    ("sensor", "working_time"),
    ("sensor", "workspace_size"),
    ("switch", "buzzer"),
    ("switch", "move_stop"),
    ("switch", "power_switch"),
    ("switch", "smoking_fan"),
    ("update", "accessory_airpump_slot2_firmware"),
    ("update", "accessory_fireextinguisherv1_5_slot4_firmware"),
    ("update", "firmware_update"),
}

# The states the maintainer's Home Assistant showed for the captured replies.
EXPECTED_STATES = {
    "binary_sensor.accessory_airpump_slot2_connected": "on",
    "binary_sensor.accessory_airpump_slot2_running": "off",
    "binary_sensor.alarm": "off",
    "binary_sensor.xcs_compat": "off",
    "light.fill_light": "on",
    "number.accessory_airpump_slot2_close_delay": "10.0",
    "number.auto_power_off": "0",
    "number.smoking_fan_duration": "120",
    "select.flame_alarm_sensitivity": "high",
    "sensor.accessory_airpump_slot2_gear": "0",
    "sensor.connection_count": "1",
    "sensor.fire_level": "0",
    "sensor.ip_address": "192.0.2.60",
    "sensor.laser_module": "Infrared",
    "sensor.laser_position_x": "0.0",
    "sensor.laser_position_y": "99.84",
    "sensor.laser_position_z": "0.0",
    "sensor.laser_power": "2",
    "sensor.riser_base": "Riser base",
    "sensor.sd_card": "not_inserted",
    "sensor.session_count": "171",
    "sensor.standby_time": "239.8",
    "sensor.status": "idle",
    "sensor.task_id": "None",
    "sensor.task_time": "0",
    "sensor.tool_runtime": "1.5",
    "sensor.working_time": "15.1",
    "sensor.workspace_size": "498 × 319 × 58 mm",
    "switch.buzzer": "on",
    "switch.move_stop": "on",
    "switch.power_switch": "on",
    "switch.smoking_fan": "on",
    "update.accessory_airpump_slot2_firmware": "off",
    "update.accessory_fireextinguisherv1_5_slot4_firmware": "off",
    "update.firmware_update": "off",
}


def entity_id(short: str) -> str:
    """``switch.buzzer`` → the S1's entity ID ``switch.xtool_s1_<serial>_buzzer``."""
    domain, key = short.split(".", 1)
    return f"{domain}.xtool_s1_{SERIAL.lower()}_{key}"


@pytest.fixture
async def s1(hass: HomeAssistant) -> AsyncIterator[tuple[MockConfigEntry, FakeS1]]:
    """The integration set up against the captured S1, with a Power switch."""
    hass.states.async_set(POWER_SWITCH, "on")
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SERIAL, title="xTool S1",
        data={
            "host": "192.0.2.60", "serial_number": SERIAL, "device_name": "xTool S1",
            "firmware_version": FIRMWARE, "laser_power_watts": 2,
            "model_key": "S1_V1", "pairing_token": "",
        },
        options={"power_switch_entity_id": POWER_SWITCH},
    )
    entry.add_to_hass(hass)
    with patch.object(DEVICE_MODELS["S1_V1"], "protocol_class", FakeS1):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry, entry.runtime_data.protocol
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


# --- Identity -----------------------------------------------------------


def test_m2003_identity() -> None:
    info = parse_m2003(M2003)
    assert info.serial_number == SERIAL
    assert info.device_name == "xTool S1"
    assert info.main_firmware == FIRMWARE
    assert info.laser_firmware == "V40.32.009.2122.01 B1"
    assert info.wifi_firmware == "V40.32.013.2224.01 B1"
    assert (info.laser.laser_type, info.laser.power_watts) == (1, 2)
    assert get_laser_type_name(info.laser.laser_type, info.laser.power_watts) == "Infrared"


async def test_device_info_includes_the_workspace() -> None:
    info = await FakeS1("192.0.2.60").get_device_info()
    assert (info.workspace_x, info.workspace_y, info.workspace_z) == (498.0, 319.0, 58.0)


def test_m2003_with_control_characters_still_parses() -> None:
    """S1 firmware puts stray control bytes into empty accessory slots."""
    info = parse_m2003(M2003.replace('"","",', '"\x15","",', 1))
    assert info.serial_number == SERIAL


def test_accessory_slots_with_stub_bytes_are_empty() -> None:
    assert parse_accessories('M1098 "\x15","","V40.208.003.3D28.01 B1"') == ["", "", "V40.208.003.3D28.01 B1"]


async def test_setup_identifies_an_s1(hass: HomeAssistant) -> None:
    """validate_connection picks the S1 model and reads its identity."""
    from custom_components.xtool.discovery import DiscoveredDevice
    from custom_components.xtool.protocols import ConnectionInfo, validate_connection

    found = DiscoveredDevice(ip="192.0.2.60", name="xTool S1", version="40.32.015")
    with (
        patch("custom_components.xtool.protocols.identify_host", return_value=found),
        patch.object(DEVICE_MODELS["S1_V1"], "protocol_class", FakeS1),
    ):
        info = await validate_connection("192.0.2.60")
    assert isinstance(info, ConnectionInfo)
    assert (info.model_id, info.protocol_version) == ("S1", "V1")
    assert (info.name, info.serial_number, info.firmware_version) == ("xTool S1", SERIAL, FIRMWARE)
    assert info.laser_power_watts == 2


# --- Entities and their states -----------------------------------------


async def test_the_s1_creates_its_entities(hass: HomeAssistant, s1) -> None:
    entry, _ = s1
    registry = er.async_get(hass)
    created = {
        (e.domain, e.unique_id.removeprefix(f"{SERIAL}_"))
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert created == EXPECTED_ENTITIES


async def test_the_entities_show_what_the_s1_reports(hass: HomeAssistant, s1) -> None:
    shown = {short: hass.states.get(entity_id(short)) for short in EXPECTED_STATES}
    assert {short: s.state if s else None for short, s in shown.items()} == EXPECTED_STATES
    assert shown["light.fill_light"].attributes["brightness"] == 255
    assert shown["select.flame_alarm_sensitivity"].attributes["options"] == ["high", "low", "off"]
    for hours in ("sensor.working_time", "sensor.standby_time", "sensor.tool_runtime"):
        assert shown[hours].attributes["unit_of_measurement"] == "h"


async def test_the_device_page(hass: HomeAssistant, s1) -> None:
    from homeassistant.helpers import device_registry as dr

    entry, _ = s1
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    device = next(d for d in devices if (DOMAIN, SERIAL) in d.identifiers)
    assert (device.manufacturer, device.model, device.sw_version) == ("xTool", "xTool S1", FIRMWARE)


async def test_the_poll_reads_every_value() -> None:
    proto = FakeS1("192.0.2.60")
    state = XtoolDeviceState()
    await proto.poll_state(state)
    assert state.status == XtoolStatus.IDLE
    assert (state.fill_light_a, state.fill_light_b, state.light_active) == (100, 100, True)
    assert (state.flame_alarm, state.fire_level) == (0, 0)
    assert (state.smoking_fan_on, state.smoking_fan_duration) == (True, 120)
    assert (state.beeper_enabled, state.move_stop_enabled) == (True, True)
    assert (state.air_assist_enabled, state.air_assist_level, state.air_assist_close_delay) == (True, 0, 10)
    assert (state.position_x, state.position_y, state.position_z) == (0.0, 99.84, 0.0)
    assert (state.task_id, state.task_time) == ("", 0)
    assert (state.sd_card_present, state.riser_base) == (False, 1)
    assert state.accessories_raw[2] == "V40.208.003.3D28.01 B1"
    assert state.accessories_raw[4] == "V40.203.002.3224.01B01"
    assert (state.working_seconds, state.session_count, state.standby_seconds, state.tool_runtime_seconds) == (
        54252, 171, 863241, 5267,
    )


async def test_the_firmware_of_each_board(hass: HomeAssistant, s1) -> None:
    entry, proto = s1
    assert await proto.get_firmware_versions(entry.runtime_data) == {
        "xTool-d2-0x20": FIRMWARE,
        "xTool-d2-0x21": "V40.32.009.2122.01 B1",
        "xTool-d2-0x22": "V40.32.013.2224.01 B1",
    }


# --- Controls: the M-codes the S1 understands ---------------------------


@pytest.mark.parametrize(("domain", "service", "short", "data", "command"), [
    ("switch", "turn_on", "switch.buzzer", {}, "M21 S1"),
    ("switch", "turn_off", "switch.buzzer", {}, "M21 S0"),
    ("switch", "turn_on", "switch.move_stop", {}, "M318 N1"),
    ("switch", "turn_off", "switch.move_stop", {}, "M318 N0"),
    ("switch", "turn_on", "switch.smoking_fan", {}, "M7 N1 D120"),
    ("switch", "turn_off", "switch.smoking_fan", {}, "M7 N0"),
    ("number", "set_value", "number.smoking_fan_duration", {"value": 60}, "M7 D60"),
    ("number", "set_value", "number.accessory_airpump_slot2_close_delay", {"value": 20}, "M1099 T20"),
    ("button", "press", "button.pause_job", {}, "M22 S1"),
    ("button", "press", "button.resume_job", {}, "M22 S0"),
    ("button", "press", "button.cancel_job", {}, "M108"),
    ("button", "press", "button.home_all", {}, "M111 S7"),
    ("button", "press", "button.home_xy", {}, "M111 S3"),
    ("button", "press", "button.home_z", {}, "M111 S2"),
    ("light", "turn_on", "light.fill_light", {}, "M13 A100B100"),
    ("light", "turn_on", "light.fill_light", {"brightness": 128}, "M13 A50B50"),
    ("light", "turn_off", "light.fill_light", {}, "M13 A0B0"),
    ("select", "select_option", "select.flame_alarm_sensitivity", {"option": "high"}, "M340 A0"),
    ("select", "select_option", "select.flame_alarm_sensitivity", {"option": "low"}, "M340 A1"),
    ("select", "select_option", "select.flame_alarm_sensitivity", {"option": "off"}, "M340 A2"),
])
async def test_controls_send_the_s1_m_codes(hass: HomeAssistant, s1, domain, service, short, data, command) -> None:
    _, proto = s1
    proto.sent.clear()
    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: entity_id(short), **data}, blocking=True)
    assert command in proto.sent


# --- Status and events ----------------------------------------------------


@pytest.mark.parametrize(("code", "status"), [
    (0, "initializing"), (1, "idle"), (2, "wifi_setup"), (3, "idle"), (4, "error_limit"),
    (7, "error_laser_module"), (9, "error_limit"), (10, "measuring"), (11, "frame_ready"),
    (12, "framing"), (13, "processing_ready"), (14, "processing"), (15, "paused"),
    (16, "firmware_update"), (17, "sleeping"), (18, "cancelling"), (19, "finished"),
    (20, "error_limit"), (21, "error_laser_control"), (22, "error_laser_module"), (24, "measure_area"),
])
async def test_m222_codes(hass: HomeAssistant, s1, code, status) -> None:
    entry, proto = s1
    proto.replies["M222"] = f"M222 S{code}"
    await _poll(hass, entry)
    assert hass.states.get(entity_id("sensor.status")).state == status


async def test_task_time_counts_only_while_working(hass: HomeAssistant, s1) -> None:
    """M815 keeps counting between jobs; only a running job shows it."""
    entry, proto = s1
    proto.replies["M815"] = "M815 T120"
    await _poll(hass, entry)
    assert hass.states.get(entity_id("sensor.task_time")).state == "0"
    proto.replies["M222"] = "M222 S14"
    await _poll(hass, entry)
    assert hass.states.get(entity_id("sensor.task_time")).state == "120"


async def _events_for(hass: HomeAssistant, entry, proto, codes: list[int]) -> list[tuple[str, str]]:
    coordinator = entry.runtime_data
    seen: list[tuple[str, str]] = []
    original = coordinator._emit_event

    def record(kind, event_type, attrs):
        seen.append((kind, event_type))
        original(kind, event_type, attrs)

    with patch.object(coordinator, "_emit_event", side_effect=record):
        for code in codes:
            proto.replies["M222"] = f"M222 S{code}"
            await _poll(hass, entry)
    return seen


@pytest.mark.parametrize(("codes", "events"), [
    ([14], [("job", "started")]),
    ([14, 15, 14, 19], [("job", "started"), ("job", "paused"), ("job", "resumed"), ("job", "finished")]),
    ([14, 18], [("job", "started"), ("job", "cancelled")]),
    ([12, 3], [("job", "framing_started"), ("job", "framing_finished")]),
    ([4], [("error", "limit")]),
    ([21], [("error", "laser_control")]),
])
async def test_job_and_error_events(hass: HomeAssistant, s1, codes, events) -> None:
    entry, proto = s1
    assert await _events_for(hass, entry, proto, codes) == events


async def test_the_job_event_reaches_its_entity(hass: HomeAssistant, s1) -> None:
    entry, proto = s1
    proto.replies["M810"] = 'M810 "job-1"'
    proto.replies["M222"] = "M222 S14"
    await _poll(hass, entry)
    event = hass.states.get(entity_id("event.job"))
    assert event.attributes["event_type"] == "started"
    assert event.attributes["task_id"] == "job-1"


async def test_a_flame_push_fires_the_fire_warning(hass: HomeAssistant, s1) -> None:
    entry, proto = s1
    proto._handle_push_frame("M340 A1")
    await _poll(hass, entry)
    assert hass.states.get(entity_id("event.fire_warning")).attributes["event_type"] == "triggered"
    assert hass.states.get(entity_id("binary_sensor.alarm")).state == "on"
    proto._handle_push_frame("M340 A0")
    await _poll(hass, entry)
    assert hass.states.get(entity_id("event.fire_warning")).attributes["event_type"] == "cleared"


async def test_pushes_fill_in_a_failed_read(hass: HomeAssistant, s1) -> None:
    entry, proto = s1
    proto._handle_push_frame("M222 S14")
    proto._handle_push_frame('M810 "job-2"')
    proto.replies["M222"] = ""
    proto.replies["M810"] = ""
    await _poll(hass, entry)
    assert hass.states.get(entity_id("sensor.status")).state == "processing"
    assert hass.states.get(entity_id("sensor.task_id")).state == "job-2"


async def test_the_fill_light_follows_its_push(hass: HomeAssistant, s1) -> None:
    """M15 pushes say whether the light is on, whatever M13's level is."""
    entry, proto = s1
    proto._handle_push_frame("M15 S0")
    await _poll(hass, entry)
    assert hass.states.get(entity_id("light.fill_light")).state == "off"


# --- Connection ----------------------------------------------------------


def test_three_quick_disconnects_switch_to_xcs_mode() -> None:
    """The XCS desktop app kicks other clients; the S1 then goes HTTP only."""
    proto = S1Protocol("192.0.2.60")
    proto._ws_connect_time = 1.0
    for _ in range(2):
        proto._track_kick()
    assert not proto.xcs_compatibility_mode
    proto._track_kick()
    assert proto.xcs_compatibility_mode


async def test_an_unreachable_s1_shows_not_responding(hass: HomeAssistant, s1) -> None:
    """With its Power switch on, an S1 that answers nothing is Not responding."""
    entry, proto = s1
    proto.replies = {}
    await _poll(hass, entry)
    assert hass.states.get(entity_id("sensor.status")).state == "not_responding"
    assert hass.states.get(entity_id("sensor.laser_position_y")).state == "unavailable"
    assert hass.states.get(entity_id("sensor.working_time")).state == "15.1"


async def test_no_poll_while_the_power_switch_is_off(hass: HomeAssistant, s1) -> None:
    entry, proto = s1
    hass.states.async_set(POWER_SWITCH, "off")
    await hass.async_block_till_done()
    proto.sent.clear()
    await _poll(hass, entry)
    assert proto.sent == []
    assert hass.states.get(entity_id("sensor.status")).state == "off"


# --- Options -------------------------------------------------------------


async def test_the_options_offer_the_s1_poll_intervals(hass: HomeAssistant, s1) -> None:
    """The S1 polls its lifetime totals (M2008) and the BLE dongle (M9098) on their own cadence."""
    entry, _ = s1
    # The options flow tells an S1 by its protocol class, which the fixture replaced
    with patch.object(DEVICE_MODELS["S1_V1"], "protocol_class", S1Protocol):
        result = await hass.config_entries.options.async_init(entry.entry_id)
    fields = {str(key) for key in result["data_schema"].schema}
    assert {"scan_interval", "stats_poll_interval", "dongle_poll_interval", "power_switch_entity_id"} <= fields
    hass.config_entries.options.async_abort(result["flow_id"])
