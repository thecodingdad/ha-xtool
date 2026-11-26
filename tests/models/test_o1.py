"""The xTool O1 Omni (DT002): job control and fill light as Studio offers them.

Studio's DT002 bundle (Studio 1.11.9) shows Pause while printing,
Continue while paused and Cancel in every job step but drying and
cutting; Continue first checks the lid, the moisturizing fluid, the
waste tank and the white-ink stirring pause. The fill light is only
switched, and its auto-off is ``fillLightAutoOffTime`` in seconds.
None of the writes is checked on an O1 yet.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.const import DOMAIN, XtoolStatus
from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.ws_v2 import protocol_dt002
from custom_components.xtool.protocols.ws_v2.protocol_dt002 import maintenance_refusal

from .o1_fake import (
    FIRMWARE,
    HOST,
    MAINTENANCE_TANK,
    PRINTING_TASK,
    SERIAL,
    TASK_ID,
    FakeO1,
    mode,
    notify,
    process,
)

O1 = DEVICE_MODELS["DT002_V2"]
CONTROL = "/v1/project/device/control"
FILL_LIGHT = "/v1/project/device/fill-light"
CONFIG = "/v1/platform/device/config"


@pytest.fixture(autouse=True)
def short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2 s wait for a head clean after a pause, shortened."""
    monkeypatch.setattr(protocol_dt002, "_AUTO_CLEAN_GRACE_SECONDS", 0.01)


@pytest.fixture
async def o1(hass: HomeAssistant) -> AsyncIterator[FakeO1]:
    """The integration set up against the fake O1, idle with a job queued."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SERIAL, title="xTool O1 Omni Printer",
        data={
            "host": HOST, "serial_number": SERIAL, "device_name": "xTool Omni Printer O1",
            "firmware_version": FIRMWARE, "laser_power_watts": 0,
            "model_key": "DT002_V2", "pairing_token": "test-token",
        },
    )
    entry.add_to_hass(hass)
    with patch.object(O1, "protocol_class", FakeO1):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        protocol = entry.runtime_data.protocol
        protocol.push(mode("Idle"))
        await hass.async_block_till_done()
        yield protocol
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{SERIAL}_{key}")
    assert entity_id is not None, f"{platform} {key}"
    return entity_id


def _state(hass: HomeAssistant, platform: str, key: str) -> str:
    return hass.states.get(_entity_id(hass, platform, key)).state


def _buttons(hass: HomeAssistant) -> dict[str, bool]:
    """Which job buttons are available."""
    return {
        key: _state(hass, "button", f"{key}_job") != "unavailable"
        for key in ("pause", "resume", "cancel")
    }


async def _print(hass: HomeAssistant, o1: FakeO1, *events: dict) -> None:
    o1._active_task = dict(PRINTING_TASK)
    o1.push(*events)
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()


# --- Entities ----------------------------------------------------------------


async def test_the_o1_controls(hass: HomeAssistant, o1: FakeO1) -> None:
    """Its own job buttons and fill light, none of the shared WS-V2 controls."""
    entries = er.async_entries_for_config_entry(
        er.async_get(hass), hass.config_entries.async_entries(DOMAIN)[0].entry_id,
    )
    controls = {
        (e.domain, e.unique_id.removeprefix(f"{SERIAL}_"))
        for e in entries if e.domain in ("button", "light", "number", "switch", "select", "fan")
    }
    assert controls == {
        ("button", "pause_job"), ("button", "resume_job"), ("button", "cancel_job"),
        ("light", "fill_light"), ("number", "fill_light_auto_off"),
    }
    assert _state(hass, "light", "fill_light") == "off"
    assert _state(hass, "number", "fill_light_auto_off") == "10"


# --- Job buttons: when Studio shows them ---------------------------------------


@pytest.mark.parametrize(("firmware_mode", "available"), [
    ("Idle", {"pause": False, "resume": False, "cancel": False}),
    ("WorkReady", {"pause": False, "resume": False, "cancel": True}),
    ("WorkPlay", {"pause": True, "resume": False, "cancel": True}),
    ("WorkRollPause", {"pause": False, "resume": True, "cancel": True}),
    ("WorkBaking", {"pause": False, "resume": False, "cancel": True}),
    # Studio shows no Cancel while drying or cutting
    ("WorkDrying", {"pause": False, "resume": False, "cancel": False}),
    ("Cutting", {"pause": False, "resume": False, "cancel": False}),
    ("Clean", {"pause": False, "resume": False, "cancel": False}),
])
async def test_the_buttons_follow_the_job(
    hass: HomeAssistant, o1: FakeO1, firmware_mode: str, available: dict,
) -> None:
    await _print(hass, o1, mode(firmware_mode))
    assert _buttons(hass) == available


async def test_no_buttons_without_a_job(hass: HomeAssistant, o1: FakeO1) -> None:
    o1.push(mode("WorkPlay"))
    await hass.async_block_till_done()
    assert _buttons(hass) == {"pause": False, "resume": False, "cancel": False}


async def test_a_pause_can_be_continued(hass: HomeAssistant, o1: FakeO1) -> None:
    await _print(hass, o1, mode("WorkPlay"), process("pause"), mode("WorkPause"))
    assert _buttons(hass) == {"pause": False, "resume": True, "cancel": True}


async def test_the_firmwares_head_clean_is_no_pause_to_continue(
    hass: HomeAssistant, o1: FakeO1,
) -> None:
    """Mid-print the O1 pauses for a flash clean and goes on by itself."""
    await _print(hass, o1, mode("WorkPlay"))
    seen: list[dict[str, bool]] = []
    for event in (
        process("pause"), mode("WorkPause"), mode("Clean"),
        notify("/v1/project/clean/start", {"head": 0, "type": "flash", "estimate_time": 4}),
        notify("/v1/project/clean/result", {"code": 0}), mode("WorkPause"),
    ):
        o1.push(event)
        await hass.async_block_till_done()
        seen.append(_buttons(hass))
    assert not any(buttons["resume"] for buttons in seen)
    assert all(buttons["cancel"] for buttons in seen)
    o1.push(mode("WorkPlay"), process("resume"))
    await hass.async_block_till_done()
    assert _buttons(hass) == {"pause": True, "resume": False, "cancel": True}


async def test_paused_after_the_clean_can_be_continued(hass: HomeAssistant, o1: FakeO1) -> None:
    """When the O1 stays paused after its clean, Continue shows."""
    await _print(
        hass, o1, mode("WorkPlay"), process("pause"), mode("WorkPause"), mode("Clean"),
        notify("/v1/project/clean/result", {"code": 0}), mode("WorkPause"),
    )
    assert _buttons(hass)["resume"]


# --- Job buttons: what they send -----------------------------------------------


async def _press(hass: HomeAssistant, key: str) -> None:
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: _entity_id(hass, "button", key)}, blocking=True,
    )


async def test_pause(hass: HomeAssistant, o1: FakeO1) -> None:
    await _print(hass, o1, mode("WorkPlay"))
    o1.sent.clear()
    await _press(hass, "pause_job")
    assert o1.writes() == [("POST", CONTROL, {"action": "PAUSE"}, None)]


async def test_continue_after_studios_checks(hass: HomeAssistant, o1: FakeO1) -> None:
    await _print(hass, o1, mode("WorkPause"))
    o1.sent.clear()
    await _press(hass, "resume_job")
    assert [url for method, url, _, _ in o1.sent if method == "GET"] == [
        "/v1/project/device/peripheral-status",
        "/v1/project/device/maintenance-tank",
        "/v1/project/device/ink-tank",
    ]
    assert o1.writes() == [("POST", CONTROL, {"action": "RESUME"}, None)]


async def test_cancel_names_the_job(hass: HomeAssistant, o1: FakeO1) -> None:
    await _print(hass, o1, mode("WorkPause"))
    o1.sent.clear()
    await _press(hass, "cancel_job")
    assert o1.writes() == [("POST", "/v1/project/task/cancel", None, {"id": TASK_ID})]


@pytest.mark.parametrize(("reply", "message"), [
    (("/v1/project/device/peripheral-status", {"lid": True}), "close the lid first"),
    (("/v1/project/device/maintenance-tank", {"waste": {"exist": True, "level": 2}}), "waste ink tank is full"),
    (("/v1/project/device/maintenance-tank", {"waste": {"exist": False, "level": 0}}), "waste ink tank is not in place"),
    (
        ("/v1/project/device/maintenance-tank", {"moisturizing": [MAINTENANCE_TANK["moisturizing"][0], {"index": 1, "exist": False}]}),
        "moisturizing fluid 2 is not in place",
    ),
    (
        ("/v1/project/device/ink-tank", {"whiteStireStatus": {"active": True, "remainSec": 12.2}}),
        "try again in 13 s",
    ),
])
async def test_continue_is_refused_as_in_studio(
    hass: HomeAssistant, o1: FakeO1, reply: tuple[str, dict], message: str,
) -> None:
    url, payload = reply
    o1.replies[("GET", url)] = payload
    await _print(hass, o1, mode("WorkPause"))
    o1.sent.clear()
    with pytest.raises(HomeAssistantError, match=message):
        await _press(hass, "resume_job")
    assert o1.writes() == []


def test_the_second_moisturizing_fluid_counts_only_with_a_second_road() -> None:
    maint = {"moisturizing": [{"exist": True, "level": 0}, {"exist": False}], "waste": {"exist": True, "level": 0}}
    assert maintenance_refusal(maint, 3) == ("job_moisturizing_missing", {"number": "2"})
    assert maintenance_refusal(maint, 0) is None
    assert maintenance_refusal(maint, None) is None


# --- Fill light ----------------------------------------------------------------


async def test_the_fill_light_is_switched(hass: HomeAssistant, o1: FakeO1) -> None:
    light = _entity_id(hass, "light", "fill_light")
    o1.sent.clear()
    await hass.services.async_call("light", "turn_on", {ATTR_ENTITY_ID: light}, blocking=True)
    assert hass.states.get(light).state == "on"
    await hass.services.async_call("light", "turn_off", {ATTR_ENTITY_ID: light}, blocking=True)
    assert o1.writes() == [
        ("POST", FILL_LIGHT, None, {"action": "on"}),
        ("POST", FILL_LIGHT, None, {"action": "off"}),
    ]


@pytest.mark.parametrize("firmware_mode", ["Scan", "Measure", "Calibration"])
async def test_no_fill_light_while_scanning(hass: HomeAssistant, o1: FakeO1, firmware_mode: str) -> None:
    o1.push(mode(firmware_mode))
    o1.sent.clear()
    with pytest.raises(HomeAssistantError, match="scans, measures or calibrates"):
        await hass.services.async_call(
            "light", "turn_on", {ATTR_ENTITY_ID: _entity_id(hass, "light", "fill_light")}, blocking=True,
        )
    assert o1.writes() == []


async def test_the_fill_light_state_is_read(hass: HomeAssistant, o1: FakeO1) -> None:
    o1.replies[("GET", FILL_LIGHT)] = {"on": True, "brightness": 1000, "autoOffSec": 600}
    await hass.config_entries.async_entries(DOMAIN)[0].runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, "light", "fill_light") == "on"


@pytest.mark.parametrize(("minutes", "seconds"), [(0, 0), (1, 60), (15, 900), (600, 36000)])
async def test_the_auto_off(hass: HomeAssistant, o1: FakeO1, minutes: int, seconds: int) -> None:
    number = _entity_id(hass, "number", "fill_light_auto_off")
    o1.sent.clear()
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: number, "value": minutes}, blocking=True,
    )
    assert o1.writes() == [("PUT", CONFIG, None, {"fillLightAutoOffTime": seconds})]
    assert hass.states.get(number).state == str(minutes)


@pytest.mark.parametrize("firmware_mode", ["WorkReady", "WorkPlay", "WorkPause", "WorkDrying", "WorkBaking"])
async def test_the_auto_off_is_locked_while_printing(
    hass: HomeAssistant, o1: FakeO1, firmware_mode: str,
) -> None:
    o1.push(mode(firmware_mode))
    o1.sent.clear()
    with pytest.raises(HomeAssistantError, match="while a job runs"):
        await hass.services.async_call(
            "number", "set_value",
            {ATTR_ENTITY_ID: _entity_id(hass, "number", "fill_light_auto_off"), "value": 5}, blocking=True,
        )
    assert o1.writes() == []


async def test_the_status_still_follows_the_mode(hass: HomeAssistant, o1: FakeO1) -> None:
    o1.push(mode("WorkPlay"))
    await hass.async_block_till_done()
    assert o1._latest["status"] is XtoolStatus.PROCESSING
