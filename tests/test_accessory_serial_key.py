"""An accessory is keyed by its serial number, never by its Bluetooth MAC.

Right after power on the IF2 2.0 often does not answer its info M-code
(M9082) while the dongle already lists it (M9098). Keyed by the MAC, it
got a second device and a second set of entities.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator


LIST = "0,38:36:0C:01:C1:96,4E736300,1;"
INFO = 'A40.213.004.4E22.01.B02 B1 C0 D4 E:"MXAK013001250917TAF0FC0" S0 Z1'
SERIAL_KEY = "DuctFanV3:MXAK013001250917TAF0FC0"


def _coordinator(hass: HomeAssistant, replies: dict[str, str | None]):
    global XtoolDeviceState
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.ws_v2.models import XTOOL_F2_ULTRA_UV_WSV2

    coordinator = build_coordinator(hass, XTOOL_F2_ULTRA_UV_WSV2)

    async def passthrough(mcode, prefix):
        return replies[mcode.split()[0]]

    coordinator.protocol.parts_control = passthrough
    return coordinator


async def test_no_info_reply_leaves_a_new_accessory_out(
    hass: HomeAssistant, config_entry: MockConfigEntry,
) -> None:
    replies = {"M9098": LIST, "M9082": None}
    coordinator = _coordinator(hass, replies)
    state = XtoolDeviceState()
    await coordinator._poll_accessories(state)
    assert state.connected_accessories == {}
    replies["M9082"] = INFO
    await coordinator._poll_accessories(state)
    assert list(state.connected_accessories) == [SERIAL_KEY]


async def test_no_info_reply_keeps_the_serial_number(
    hass: HomeAssistant, config_entry: MockConfigEntry,
) -> None:
    replies = {"M9098": LIST, "M9082": INFO}
    coordinator = _coordinator(hass, replies)
    state = XtoolDeviceState()
    await coordinator._poll_accessories(state)
    before = state.connected_accessories[SERIAL_KEY].fields
    replies["M9082"] = None
    await coordinator._poll_accessories(state)
    assert list(state.connected_accessories) == [SERIAL_KEY]
    # The last values are kept
    assert state.connected_accessories[SERIAL_KEY].fields["version"] == before["version"]
