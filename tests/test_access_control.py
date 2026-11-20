"""Access control is offered only where Studio offers it.

Studio's ``checkSupportMD`` shows Access control only when the SKU in
``/v1/device/machineInfo`` starts with one of the model's EU SKUs (the P3:
its EU machine style). The switch is not made on other units.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from .features import requires

pytestmark = requires("access_control_sku")


def _model(model_id: str):
    from custom_components.xtool.protocols import DEVICE_MODELS

    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == model_id and m.protocol_version == "V2"
    )


def _supported(model, sku="", style=""):
    from custom_components.xtool.coordinator import XtoolCoordinator

    class Unit(SimpleNamespace):
        access_control_supported = XtoolCoordinator.access_control_supported
        eu_unit = XtoolCoordinator.eu_unit

    return Unit(model=model, sku=sku, machine_style=style).access_control_supported


@pytest.mark.parametrize(
    ("model_id", "skus"),
    [
        ("F2UltraUV", ("P0101000396", "P0101000397")),
        ("F2Ultra", ("P0101000661", "P0101000659")),
        ("F2UltraSingle", ("P0101000665", "P0101000664")),
        ("F2", ("P0101000600", "P0101000601")),
        ("F1Ultra", ("P0101000604", "P0101000599")),
    ],
)
def test_the_skus_studio_checks(model_id, skus) -> None:
    model = _model(model_id)
    assert model.has_md_mode and model.access_control_skus == skus
    for sku in skus:
        assert _supported(model, sku + "251108100069") is True


def test_the_f2_ultra_uv_here_has_none() -> None:
    # The SKU of the F2 Ultra UV this was tested on: Studio hides it
    assert _supported(_model("F2UltraUV"), "P0101000393251108100069") is False


def test_not_known_until_the_sku_is() -> None:
    assert _supported(_model("F2UltraUV")) is None


def test_p3_by_machine_style() -> None:
    p3 = _model("P3")
    assert _supported(p3, "P0000", "lowCost_europe") is True
    assert _supported(p3, "P0000", "lowCost") is False


def test_models_without_it() -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    p2 = next(m for m in DEVICE_MODELS.values() if m.model_id == "P2")
    assert _supported(p2, "P0101000396") is False


async def test_machine_info_gives_the_sku() -> None:
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    proto.request = AsyncMock(return_value={
        "sn": "MXF0000", "sku": "P0101000396000000000000", "machineStyle": "standard",
    })
    info = await proto.get_device_info()
    assert info.sku == "P0101000396000000000000"
    assert info.machine_style == "standard"


def test_the_switch_follows_it() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "custom_components" / "xtool"
    entities = (root / "protocols" / "ws_v2" / "entities.py").read_text(encoding="utf-8")
    assert "if coordinator.access_control_supported is not False:" in entities
    init = (root / "__init__.py").read_text(encoding="utf-8")
    assert 'coordinator.sku = entry.data.get("sku", "")' in init


def test_the_device_page_shows_the_sku(hass, config_entry) -> None:
    from custom_components.xtool.entity import XtoolEntity

    from .conftest import build_coordinator

    coordinator = build_coordinator(hass, _model("F2UltraUV"))
    assert XtoolEntity(coordinator).device_info.get("model_id") is None
    coordinator.sku = "P0101000393251108100069"
    assert XtoolEntity(coordinator).device_info["model_id"] == "P0101000393"
    coordinator.sku = "P0101000396251108100069"
    assert XtoolEntity(coordinator).device_info["model_id"] == "P0101000396 (EU)"


def test_disabled_by_default(hass, config_entry) -> None:
    from custom_components.xtool.protocols.ws_v2.entities import build_wsv2_switches

    from .conftest import build_coordinator

    coordinator = build_coordinator(hass, _model("F2UltraUV"))
    coordinator.sku = "P0101000396251108100069"
    switch = next(e for e in build_wsv2_switches(coordinator) if e.translation_key == "md_mode")
    assert switch.entity_registry_enabled_default is False
    coordinator.sku = "P0101000393251108100069"
    assert not [e for e in build_wsv2_switches(coordinator) if e.translation_key == "md_mode"]
