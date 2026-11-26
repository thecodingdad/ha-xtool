"""The SafetyPro AP2 as Studio's accessory runtime has it: LargePurifier (0x45).

Lasers that list their accessories with M9098 report the AP2 as
``45…``; the replies here are what an Apparel Printer (DT001, firmware
40.100.028.00.ht6) answered with an AP2 connected and switched off (#36,
by corillis; MAC and serial replaced). Studio's adapters also change the
tunnel's ``link`` per device: ``linkMotion`` on the DT001, ``linkDongle``
on the DT003.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.protocols import DEVICE_MODELS
from custom_components.xtool.protocols.accessories import get_definition
from custom_components.xtool.protocols.base import XtoolDeviceState

from .conftest import build_coordinator

CONNECTED = "M9098 0,02:00:00:00:00:45,45736000,1;"
SWITCHED_OFF = "M9098 0,02:00:00:00:00:45,45736000,0;"
INFO = 'V40.212.007.4528.01 B03 C2 D1 H77 I89 J97 K93 L93 S0 E:"MXTEST0001AP2"'


def _coordinator(hass: HomeAssistant, replies: dict[str, str | None]):
    coordinator = build_coordinator(hass, DEVICE_MODELS["DT001_V2"])
    asked: list[str] = []

    async def parts_control(mcode, prefix, timeout=6.0):
        asked.append(mcode)
        return replies.get(mcode.split()[0])

    coordinator.protocol.parts_control = parts_control
    return coordinator, asked


async def test_the_ap2_as_the_dt001_lists_it(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    coordinator, _ = _coordinator(hass, {"M9098": CONNECTED, "M9033": INFO})
    state = XtoolDeviceState()
    await coordinator._poll_accessories(state)
    assert list(state.connected_accessories) == ["LargePurifier:MXTEST0001AP2"]
    ap2 = state.connected_accessories["LargePurifier:MXTEST0001AP2"]
    assert ap2.fields["filter_pre"] == 77
    assert ap2.fields["gear"] == "auto"


async def test_an_ap2_that_is_off_is_not_asked(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """The DT001 keeps a paired AP2 in its list, with status 0, while it is off."""
    coordinator, asked = _coordinator(hass, {"M9098": SWITCHED_OFF})
    state = XtoolDeviceState()
    await coordinator._poll_accessories(state)
    assert state.connected_accessories == {}
    assert asked == ["M9098"]


def test_the_ap2_definition_is_studios() -> None:
    ap2 = get_definition("LargePurifier")
    assert ap2.prefix == bytes([69, 115, 96, 1, 0])
    assert ap2.firmware_content_id == "xTool-bigPurifier-firmware"


@pytest.mark.parametrize(("model_key", "link"), [
    ("DT001_V2", "linkMotion"), ("DT003_V2", "linkDongle"), ("P3_V2", "uart485"),
])
async def test_the_accessory_link_on_v2_firmware(model_key: str, link: str) -> None:
    model = DEVICE_MODELS[model_key]
    protocol = model.protocol_class("192.0.2.43")
    protocol.set_model(model)
    sent: list[dict] = []

    async def request(url, method="GET", params=None, data=None, timeout=10.0, **_):
        sent.append(data)
        return {}

    protocol.request = request
    await protocol.parts_control("M9098", bytes([71, 115, 100, 1, 0]))
    assert sent[0]["link"] == link


@pytest.mark.parametrize(("model_key", "link"), [
    ("DT001_V1", "linkMotion"), ("DT003_V1", "linkDongle"), ("P2S_V1", "uart485"),
])
async def test_the_accessory_link_on_v1_firmware(model_key: str, link: str) -> None:
    model = DEVICE_MODELS[model_key]
    protocol = model.protocol_class("192.0.2.43")
    protocol.set_model(model)
    sent: list = []

    async def post_json(path, data=None, timeout=5.0):
        sent.append(data)
        return None

    protocol._post_json = post_json
    await protocol.passthrough("M9098", bytes([71, 115, 100, 1, 0]))
    assert sent[0]["link"] == link
