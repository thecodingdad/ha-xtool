"""Which model a discovered name stands for.

Every model's discovery patterns share one namespace (``F2`` is in
``F2 Ultra``, which is in ``F2 Ultra UV``): adding or changing one model's
pattern must not take another model's devices.
"""

from __future__ import annotations

import pytest

from custom_components.xtool.protocols import DEVICE_MODELS, detect_model, detect_models


def _patterns() -> list[tuple[str, str]]:
    return [
        (key, pattern)
        for key, model in sorted(DEVICE_MODELS.items())
        for pattern in (model.discovery_match or (model.model_id,))
    ]


@pytest.mark.parametrize(("key", "pattern"), _patterns())
def test_each_discovery_name_finds_its_own_model(key: str, pattern: str) -> None:
    model = DEVICE_MODELS[key]
    candidates = [m for m in detect_models(f"xTool {pattern}") if m.protocol_version == model.protocol_version]
    assert candidates and candidates[0] is model, [f"{m.model_id}_{m.protocol_version}" for m in candidates]


def test_an_s1_is_only_an_s1() -> None:
    assert detect_models("xTool S1") == [DEVICE_MODELS["S1_V1"]]
    assert detect_model("xTool S1") is DEVICE_MODELS["S1_V1"]


def test_an_unknown_name_finds_no_model() -> None:
    assert detect_models("Some Laser 3000") == []
    assert detect_model("Some Laser 3000").model_id == "unknown"


def test_v1_comes_first_where_both_firmwares_exist() -> None:
    """detect_model answers the V1 model; validate_connection probes for V2."""
    for key, model in DEVICE_MODELS.items():
        if model.protocol_version != "V2" or f"{model.model_id}_V1" not in DEVICE_MODELS:
            continue
        pattern = (model.discovery_match or (model.model_id,))[0]
        assert detect_model(f"xTool {pattern}").protocol_version == "V1", key
