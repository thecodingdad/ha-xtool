"""A laser whose Power switch is off is not polled; a failed poll says why."""

from __future__ import annotations

import logging
import re
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from .conftest import POWER_SWITCH, build_coordinator
from .features import requires


def _models():
    from custom_components.xtool.protocols import DEVICE_MODELS

    # One model of each family (coordinator class)
    seen, models = set(), []
    for model in DEVICE_MODELS.values():
        if model.coordinator_class not in seen:
            seen.add(model.coordinator_class)
            models.append(model)
    return models


@requires("skip_polling_while_off")
async def test_no_poll_while_the_power_switch_is_off(hass: HomeAssistant, config_entry) -> None:
    from custom_components.xtool.protocols.base import XtoolDeviceState

    for model in _models():
        coordinator = build_coordinator(hass, model)
        coordinator.protocol.connected = True
        coordinator.protocol.disconnect = AsyncMock()
        coordinator.data = XtoolDeviceState(available=True, firmware_version="1.2.3")
        polled = []

        async def poll(polled=polled):
            polled.append(True)
            return XtoolDeviceState(available=True)

        coordinator._async_poll = poll
        hass.states.async_set(POWER_SWITCH, "off")
        state = await coordinator._async_update_data()
        assert polled == [], model.model_id
        # Last values kept, marked unavailable; the connection is closed
        assert (state.available, state.firmware_version) == (False, "1.2.3")
        coordinator.protocol.disconnect.assert_awaited_once()
        hass.states.async_set(POWER_SWITCH, "on")
        assert (await coordinator._async_update_data()).available is True
        assert polled == [True]


@requires("poll_error_type")
async def test_a_failed_poll_names_its_error(hass: HomeAssistant, config_entry, caplog) -> None:
    hass.states.async_set(POWER_SWITCH, "on")
    for model in _models():
        coordinator = build_coordinator(hass, model)
        coordinator.protocol.connected = False
        coordinator.protocol.connect = AsyncMock(side_effect=TimeoutError())  # no message
        coordinator.protocol.disconnect = AsyncMock()
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            try:
                await coordinator._async_update_data()
            except Exception:  # noqa: BLE001
                pass
        # The error with its type, such as TimeoutError()
        assert re.search(r"(poll failed|Error polling).*: \w+Error\(", caplog.text), model.model_id
