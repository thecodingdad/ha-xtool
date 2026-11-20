"""The P2's right and left fill lights stay in step with Studio's one Fill light brightness."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import build_coordinator
from .features import requires

pytestmark = requires("p2_studio_brightness")


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry):
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest import XTOOL_P2

    coord = build_coordinator(hass, XTOOL_P2)
    coord.protocol.set_config = AsyncMock(return_value=True)
    coord.protocol.set_fill_light_level = AsyncMock()
    state = XtoolDeviceState()
    state.available = True
    state.device_config = {"fillLightBrightness": 153}
    state.fill_light_levels = {1: 0, 2: 153, 3: 153}
    coord.data = state
    return coord


def by_key(entities):
    for entity in entities:
        entity.async_write_ha_state = MagicMock()
    # On the close camera's device the close fill light is just "Fill light"
    return {
        ("fill_light_1" if e.translation_key == "fill_light" else e.translation_key): e
        for e in entities
    }


async def test_a_side_light_writes_the_brighter_of_the_two(coordinator) -> None:
    lights = by_key(coordinator.build_lights())
    await lights["fill_light_2"].async_turn_on(brightness=200)
    coordinator.protocol.set_config.assert_awaited_with(fillLightBrightness=200)
    coordinator.protocol.set_config.reset_mock()
    # Dimmer than the other side light: Studio keeps the brighter one
    await lights["fill_light_3"].async_turn_on(brightness=100)
    coordinator.protocol.set_config.assert_not_awaited()
    # Both off: Studio keeps its last brightness
    await lights["fill_light_2"].async_turn_off()
    coordinator.protocol.set_config.assert_awaited_with(fillLightBrightness=100)
    coordinator.protocol.set_config.reset_mock()
    await lights["fill_light_3"].async_turn_off()
    coordinator.protocol.set_config.assert_not_awaited()


async def test_the_close_light_leaves_studio_alone(coordinator) -> None:
    await by_key(coordinator.build_lights())["fill_light_1"].async_turn_on(brightness=50)
    coordinator.protocol.set_config.assert_not_awaited()


async def test_a_change_in_studio_sets_both_side_lights(monkeypatch) -> None:
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("192.0.2.47")
    proto.set_fill_light_level = AsyncMock()
    state = XtoolDeviceState()
    state.fill_light_levels = {1: 0, 2: 153, 3: 0}
    # The first read only learns the value
    await proto._follow_studio_brightness(state, 153)
    proto.set_fill_light_level.assert_not_awaited()
    await proto._follow_studio_brightness(state, 204)
    assert [c.args for c in proto.set_fill_light_level.await_args_list] == [(2, 204), (3, 204)]
    assert state.fill_light_levels == {1: 0, 2: 204, 3: 204}


async def test_our_own_write_is_not_followed(monkeypatch) -> None:
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("192.0.2.47")
    proto.set_fill_light_level = AsyncMock()
    monkeypatch.setattr(proto, "_post_json", AsyncMock(return_value={"code": 0}))
    state = XtoolDeviceState()
    state.fill_light_levels = {1: 0, 2: 200, 3: 100}
    await proto._follow_studio_brightness(state, 153)
    assert await proto.set_config(fillLightBrightness=200)
    await proto._follow_studio_brightness(state, 200)
    proto.set_fill_light_level.assert_not_awaited()
