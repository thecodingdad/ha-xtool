"""Focus only with the surface lens, as in xTool Studio.

With the inner engraving lens the job sets the head's height from the
material's size, so the focus height is not a thickness: it is Unknown,
and moves and Autofocus are refused. The lens push updates the lens.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xtool.const import XtoolStatus

from .features import requires
from .test_focus import _focus_entity, _protocol

pytestmark = requires("focus_lens")


@pytest.mark.parametrize(("lens", "refused"), [("big", False), ("small", True), ("none", True), (None, False)])
def test_focus_refused_without_the_surface_lens(lens: str | None, refused: bool) -> None:
    proto = _protocol()
    proto._device_mode = "P_IDLE"
    proto._latest["status"] = XtoolStatus.IDLE
    if lens is not None:
        proto._latest["engraving_lens"] = lens
    assert (proto.focus_refusal() is not None) is refused


def test_inner_lens_reason_names_the_lens() -> None:
    proto = _protocol()
    proto._device_mode = "P_IDLE"
    proto._latest["status"] = XtoolStatus.IDLE
    proto._latest["engraving_lens"] = "small"
    assert "inner engraving lens" in proto.focus_refusal()


def test_lens_push_updates_the_lens() -> None:
    proto = _protocol()
    proto._dispatch_push({"url": "/cj_check/status", "data": {"module": "CJ_CHECK", "type": "MODE_CHANGE", "info": "small"}})
    assert proto._latest["engraving_lens"] == "small"
    proto._dispatch_push({"url": "/cj_check/status", "data": {"module": "CJ_CHECK", "type": "MODE_CHANGE", "info": "big"}})
    assert proto._latest["engraving_lens"] == "big"
    proto._dispatch_push({"url": "/cj_check/status", "data": {"module": "CJ_CHECK", "type": "MODE_CHANGE", "info": "odd"}})
    assert proto._latest["engraving_lens"] == "big"


@pytest.mark.parametrize(("lens", "shown"), [("big", 36.5), ("small", None), ("none", None), (None, 36.5)])
def test_focus_height_only_with_the_surface_lens(hass: HomeAssistant, config_entry, lens: str | None, shown: float | None) -> None:
    entity, _ = _focus_entity(hass, 36.5)
    entity.coordinator.data.engraving_lens = lens
    assert entity.native_value == shown
