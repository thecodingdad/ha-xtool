"""Tests for the IF2 2.0 fan (issue #4).

The commands and the push fields follow xTool Studio: its setFanGearV3
command (``M9064 <A|B><gear> C<mode>``) and its M9064 report handler
(mode in ``C``, Manual gear in ``A``, Auto gear in ``B``). The push
samples are from Studio's own log of an F2 Ultra UV with an IF2 2.0.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import SERIAL, build_coordinator
from .features import has, requires

pytestmark = requires("if2_fan_v3")

# Before the single Auto preset, Auto had a regular and a quiet preset
SINGLE_AUTO = has("if2_fan_v3") and has("if2_fan_single_auto")
AUTO = "Auto" if SINGLE_AUTO else "Auto Regular"
two_presets = pytest.mark.skipif(SINGLE_AUTO, reason="the fan has one Auto preset")

IF2_SN = "MXAK013001250917TAF0FC0"


def duct_fan():
    from custom_components.xtool.protocols.accessories import duct_fan

    return duct_fan


@pytest.mark.parametrize(
    ("gear", "command"),
    [(0, "M9064 A0 C2"), (1, "M9064 A1 C3"), (2, "M9064 A2 C3"), (3, "M9064 A3 C3"), (4, "M9064 A4 C3")],
)
def test_manual_commands_carry_the_mode(gear: int, command: str) -> None:
    """Each Manual gear (and Off) names its mode, as Studio does."""
    assert duct_fan().fan_v3_manual_mcode(gear) == command


@two_presets
def test_auto_commands() -> None:
    """Auto runs at Studio's default gear 3, quiet one gear less."""
    df = duct_fan()
    assert df.fan_v3_auto_mcode("Auto Regular") == "M9064 B3 C4"
    assert df.fan_v3_auto_mcode("Auto Quiet") == "M9064 B2 C4"
    assert df.fan_v3_auto_mcode("Turbo") is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("A1 B0 C2 D0 S0", {"mode_class": 2, "current_gear": 0, "job_active": False}),
        ("A1 B1 C3 D0 S0", {"mode_class": 3, "current_gear": 1, "job_active": False}),
        ("A1 B2 C3 D1 S0", {"mode_class": 3, "current_gear": 1, "job_active": True}),
        ("A4 B3 C4 D0 S0", {"mode_class": 4, "auto_gear": 3, "auto_submode": "Auto Regular", "job_active": False}),
        ("A4 B4 C4 D1 S0", {"mode_class": 4, "auto_gear": 4, "auto_submode": "Auto Regular", "job_active": True}),
        ("A4 B2 C4 D1 S0", {"mode_class": 4, "auto_gear": 2, "auto_submode": "Auto Quiet", "job_active": True}),
        ("A4 B1 C4 D0 S0", {"mode_class": 4, "auto_gear": 1, "auto_submode": "Auto Quiet", "job_active": False}),
        ("A1 B0 C4 D0 S0", {"mode_class": 4, "auto_gear": 0, "job_active": False}),
    ],
)
def test_push_reads_mode_gear_and_job(body: str, expected: dict) -> None:
    """The push names the mode in C, the Auto gear in B and a running job in D."""
    fields = duct_fan().parse_fan_v3_push(body)
    fields.pop("alert", None)
    if SINGLE_AUTO:  # one Auto preset: the push names no preset
        expected = {k: v for k, v in expected.items() if k != "auto_submode"}
    assert fields == expected


def test_push_keeps_the_alert() -> None:
    """S carries the stall (1) and cleaning (2) alerts."""
    assert duct_fan().parse_fan_v3_push("A2 B1 C3 D0 S2")["alert"] == 2


@pytest.mark.parametrize(
    ("body", "mode", "gear"),
    [
        ('A40.213.004.4E22.01.B02 B4 C2 D2 E:"SN" S0 Z1', 2, 0),
        ('A40.213.004.4E22.01.B02 B2 C3 D3 E:"SN" S0 Z1', 3, 2),
        ('A40.213.004.4E22.01.B02 B3 C4 D4 E:"SN" S0 Z0', 4, None),
    ],
)
def test_poll_reads_the_mode_from_d(body: str, mode: int, gear: int | None) -> None:
    """The M9082 poll names the mode in D, and B is the gear only in Manual."""
    fields = duct_fan().parse_fan_v3_info(body)
    assert fields["mode_class"] == mode
    assert fields.get("current_gear") == gear
    assert fields["version"] == "40.213.004.4E22.01.B02"


def test_if2_builds_one_fan_and_no_select_or_speed_sensor() -> None:
    """The fan entity replaces the Fan select and the Current speed sensor."""
    from custom_components.xtool.protocols.accessories import ACCESSORY_DEFINITIONS

    definition = ACCESSORY_DEFINITIONS["DuctFanV3"]
    platforms = [(spec.platform, spec.key) for spec in definition.entities]
    assert ("fan", "fan") in platforms
    assert ("select", "mode_speed") not in platforms
    assert ("sensor", "current_speed") not in platforms


def _wsv2_f2_ultra_uv():
    from custom_components.xtool.protocols import DEVICE_MODELS

    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == "F2UltraUV" and m.protocol_version == "V2"
    )


@pytest.fixture
def fan(hass: HomeAssistant, config_entry: MockConfigEntry):
    """The IF2 2.0 fan entity on an F2 Ultra UV, with the IF2 in Manual Off."""
    from custom_components.xtool.protocols.accessories.entities import build_accessory_entities
    from custom_components.xtool.protocols.base import AccessoryState, XtoolDeviceState

    coordinator = build_coordinator(hass, _wsv2_f2_ultra_uv())
    coordinator.protocol.parts_control = AsyncMock(return_value="ok")
    accessory = AccessoryState(
        type_id="DuctFanV3",
        sn=IF2_SN,
        fields=duct_fan().parse_fan_v3_info('A40.213.004 B4 C2 D2 E:"SN" S0 Z1'),
    )
    state = XtoolDeviceState()
    state.available = True
    state.connected_accessories = {f"DuctFanV3:{IF2_SN}": accessory}
    coordinator.data = state
    entity = next(
        e for e in build_accessory_entities(coordinator, accessory)
        if getattr(e, "_xtool_platform", None) == "fan"
    )
    entity.async_write_ha_state = lambda: None
    return entity, coordinator.protocol.parts_control, accessory


def _sent(parts_control: AsyncMock) -> str:
    return parts_control.await_args.args[0]


async def test_fan_starts_off(fan) -> None:
    entity, _, _ = fan
    assert entity.is_on is False
    assert entity.percentage == 0
    assert entity.preset_mode is None


@pytest.mark.parametrize(
    ("percentage", "command", "gear"),
    [(25, "M9064 A1 C3", 1), (50, "M9064 A2 C3", 2), (100, "M9064 A4 C3", 4)],
)
async def test_fan_speed_from_off(fan, percentage: int, command: str, gear: int) -> None:
    """A speed set while Off turns the IF2 on at that gear."""
    entity, parts_control, accessory = fan
    await entity.async_set_percentage(percentage)
    assert _sent(parts_control) == command
    assert entity.is_on is True
    assert entity.percentage == percentage
    # The next poll agrees with what was written.
    accessory.fields.update(
        duct_fan().parse_fan_v3_info(f'A40.213.004 B{gear} C3 D3 E:"SN" S0 Z1')
    )
    assert entity.percentage == percentage


@two_presets
async def test_fan_presets_while_idle_stay_off(fan) -> None:
    """Choosing a preset with no job running shows it, with the fan off."""
    entity, parts_control, _ = fan
    await entity.async_set_preset_mode("Auto Quiet")
    assert _sent(parts_control) == "M9064 B2 C4"
    assert entity.preset_mode == "Auto Quiet"
    assert entity.is_on is False
    assert entity.percentage == 0
    await entity.async_set_preset_mode("Auto Regular")
    assert _sent(parts_control) == "M9064 B3 C4"
    assert entity.preset_mode == "Auto Regular"
    assert entity.is_on is False


@two_presets
async def test_auto_job_shows_the_real_speed(fan) -> None:
    """Captured from an F2 Ultra UV: the job runs at the speed saved in it,
    HA's preset changes apply live, and the fan is off after the post-run."""
    entity, parts_control, accessory = fan
    await entity.async_set_preset_mode("Auto Regular")

    def push(body: str) -> None:
        accessory.fields.update(duct_fan().parse_fan_v3_push(body))

    push("A1 B3 C4 D1 S0")
    push("A1 B2 C4 D1 S0")  # the job was saved with Auto Quiet
    assert entity.is_on is True
    assert entity.percentage == 50
    assert entity.preset_mode == "Auto Quiet"
    await entity.async_set_preset_mode("Auto Regular")
    push("A1 B3 C4 D1 S0")
    assert entity.percentage == 75
    assert entity.preset_mode == "Auto Regular"
    push("A1 B0 C4 D0 S0")  # post-run over
    assert entity.is_on is False
    assert entity.percentage == 0
    assert entity.preset_mode == "Auto Regular"


def test_poll_keeps_the_job_flag() -> None:
    """The M9082 poll has no job flag, so the push's flag survives it."""
    import inspect

    from custom_components.xtool import coordinator

    assert '"job_active"' in inspect.getsource(coordinator)


async def test_fan_off_and_back_on_restores_the_last_gear(fan) -> None:
    entity, parts_control, _ = fan
    await entity.async_set_percentage(75)
    await entity.async_turn_off()
    assert _sent(parts_control) == "M9064 A0 C2"
    assert entity.is_on is False
    await entity.async_turn_on()
    assert _sent(parts_control) == "M9064 A3 C3"
    assert entity.percentage == 75


async def test_turn_on_is_manual_at_the_last_spinning_speed(fan) -> None:
    """Turning on leaves Auto, at the speed the fan last spun at in either mode."""
    entity, parts_control, accessory = fan
    await entity.async_set_preset_mode(AUTO)
    accessory.fields.update(duct_fan().parse_fan_v3_push("A1 B2 C4 D1 S0"))
    entity._handle_coordinator_update()
    await entity.async_turn_off()
    assert _sent(parts_control) == "M9064 A0 C2"
    assert entity.preset_mode is None
    await entity.async_turn_on()
    assert _sent(parts_control) == "M9064 A2 C3"
    assert entity.preset_mode is None
    assert entity.percentage == 50


async def test_speed_during_an_auto_job_leaves_auto(fan) -> None:
    entity, parts_control, accessory = fan
    await entity.async_set_preset_mode(AUTO)
    accessory.fields.update(duct_fan().parse_fan_v3_push("A1 B3 C4 D1 S0"))
    await entity.async_set_percentage(25)
    assert _sent(parts_control) == "M9064 A1 C3"
    assert entity.preset_mode is None
    assert entity.percentage == 25


@two_presets
async def test_fan_follows_a_studio_change(fan) -> None:
    """A change made in Studio arrives as a push and shows at once."""
    entity, _, accessory = fan
    accessory.fields.update(duct_fan().parse_fan_v3_push("A2 B1 C3 D0 S0"))
    assert entity.is_on is True
    assert entity.percentage == 50
    accessory.fields.update(duct_fan().parse_fan_v3_push("A2 B3 C4 D0 S0"))
    assert entity.preset_mode == "Auto Regular"
    assert entity.is_on is False


async def test_retired_if2_entities_are_removed(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The old Fan select and Current speed sensor leave the registry."""
    from custom_components.xtool.const import DOMAIN
    from custom_components.xtool.protocols.accessories.entities import build_accessory_entities
    from custom_components.xtool.protocols.base import AccessoryState

    registry = er.async_get(hass)
    prefix = f"{SERIAL}_accessory_ductfanv3_{IF2_SN}_"
    select = registry.async_get_or_create(
        "select", DOMAIN, prefix + "mode_speed", config_entry=config_entry
    )
    sensor = registry.async_get_or_create(
        "sensor", DOMAIN, prefix + "current_speed", config_entry=config_entry
    )
    coordinator = build_coordinator(hass, _wsv2_f2_ultra_uv())
    accessory = AccessoryState(type_id="DuctFanV3", sn=IF2_SN, fields={"mode_class": 2})
    build_accessory_entities(coordinator, accessory)
    assert registry.async_get(select.entity_id) is None
    assert registry.async_get(sensor.entity_id) is None


@pytest.mark.skipif(not SINGLE_AUTO, reason="the fan has two Auto presets")
def test_one_auto_preset() -> None:
    """Auto keeps the IF2's gear; each job sets its own, as in Studio."""
    df = duct_fan()
    assert df.FANV3_PRESETS == ("Auto",)
    assert df.fan_v3_auto_mcode(3) == "M9064 B3 C4"
    assert df.fan_v3_auto_mcode(2) == "M9064 B2 C4"


@pytest.mark.skipif(not SINGLE_AUTO, reason="the fan has two Auto presets")
async def test_auto_keeps_the_gear_and_the_job_sets_it(fan) -> None:
    entity, parts_control, accessory = fan
    assert entity.preset_modes == ["Auto"]
    await entity.async_set_preset_mode("Auto")
    assert _sent(parts_control) == "M9064 B3 C4"  # 3 until a gear is seen
    assert entity.preset_mode == "Auto"
    assert entity.is_on is False

    def push(body: str) -> None:
        accessory.fields.update(duct_fan().parse_fan_v3_push(body))

    push("A1 B2 C4 D1 S0")  # a job sent with Studio's quiet work mode
    assert entity.is_on is True and entity.percentage == 50 and entity.preset_mode == "Auto"
    await entity.async_set_percentage(100)
    assert entity.preset_mode is None
    await entity.async_set_preset_mode("Auto")
    assert _sent(parts_control) == "M9064 B2 C4"  # back to the job's gear
    push("A1 B0 C4 D0 S0")  # post-run over
    assert entity.is_on is False and entity.preset_mode == "Auto"
    with pytest.raises(Exception):
        await entity.async_set_preset_mode("Auto Quiet")
