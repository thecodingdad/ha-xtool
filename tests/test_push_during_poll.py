"""A push that arrives while a WS-V2 poll reads the accessories wins.

The poll reads the laser, applies the pushes it has, then reads the BT
accessories. A push in that last read (an F2 Ultra UV falling asleep) is
newer than the poll; the poll used to put back the status it replaced, so
the status went Sleeping, Idle, Sleeping within one poll.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from .conftest import build_coordinator
from .features import requires

pytestmark = requires("push_after_accessory_poll")


async def test_push_during_the_accessory_read_wins(hass: HomeAssistant) -> None:
    from custom_components.xtool.const import XtoolStatus
    from custom_components.xtool.protocols import DEVICE_MODELS

    model = next(m for m in DEVICE_MODELS.values() if m.model_id == "F2UltraUV" and m.protocol_version == "V2")
    coordinator = build_coordinator(hass, model)
    coordinator._device_info_fetched = True
    protocol = coordinator.protocol
    protocol.connected = True
    latest: dict = {}

    def apply_latest(state) -> None:
        if "status" in latest:
            state.status = latest["status"]

    async def poll_state(state) -> None:
        state.status = XtoolStatus.IDLE
        latest["status"] = XtoolStatus.IDLE
        apply_latest(state)

    async def poll_accessories(state) -> None:
        latest["status"] = XtoolStatus.SLEEPING  # the /work/mode P_SLEEP push

    protocol._apply_latest_to_state = apply_latest
    protocol.poll_state = poll_state
    coordinator._poll_accessories = poll_accessories
    coordinator._emit_status_transition_events = MagicMock()
    coordinator._emit_fire_warning_if_changed = MagicMock()
    coordinator._drain_protocol_events = MagicMock()
    coordinator._dispatch_new_accessories = MagicMock()

    state = await coordinator._async_update_data()

    assert state.available
    assert state.status == XtoolStatus.SLEEPING
    # Status transition events see the final status, not the poll's
    assert coordinator._emit_status_transition_events.call_args.args[1].status == XtoolStatus.SLEEPING
