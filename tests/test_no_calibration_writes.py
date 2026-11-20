"""The integration never changes what xTool Studio calibrates on the laser.

Studio stores its calibrations on the laser: the camera grid
(global_calib.json, imgConfigGlobal*), the focus and Z offsets, the
laser offsets and angles, and the conveyor rate. If Home Assistant
changed any of them, the laser would no longer match Studio. These
tests fail if any control of any model sends one of them, or if the
code names a calibration key or a calibration write at all.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool.protocols import DEVICE_MODELS

from .conftest import build_coordinator

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "xtool"

# Config keys that Studio's calibration screens write (GS009-CLASS-4 and
# the other WS-V2 bundles).
CALIBRATION_KEYS = (
    "globalOffsetZ",
    "zPositionCompensate",
    "zPositionCompensateSmall",
    "zPositionSecondCompensate",
    "innerZOffset",
    "secondOffsetFlag",
    "laserOffsetUv",
    "laserOffsetBlue",
    "laserAngleBlue",
    "laserAngleRed",
    "focusPower",
    "ConveyorURate",
    "ConveyorAngleCompensate",
    "imgConfigGlobalCalib",
    "imgConfigGlobalIr",
)

# Requests that move the head for calibration, or write calibration data.
CALIBRATION_WRITES = (
    "/v1/laser-head/focus/parameter",  # Studio's setFocusInfo
    "/v1/motion_control",  # motion delay parameters
    "setUserConfigs",
)


def _words(value: Any) -> str:
    """Every string, key and value inside a call's arguments, as one text."""
    if isinstance(value, dict):
        return " ".join(f"{k} {_words(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_words(v) for v in value)
    return str(value)


def _touches_calibration(text: str) -> list[str]:
    found = [key for key in CALIBRATION_KEYS if re.search(rf"\b{key}\b", text)]
    found += [write for write in CALIBRATION_WRITES if write in text]
    return found


def test_source_names_no_calibration_key_or_write() -> None:
    """No file of the integration names a calibration key or a calibration write."""
    problems = []
    for path in sorted(COMPONENT.rglob("*")):
        if path.suffix not in (".py", ".js", ".json") or "translations" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".py":
            # Comments may name a key to say it is left alone.
            text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        if path.name == "protocol.py" and f"async def {READ_ONLY_FUNCTION}(" in text:
            # The Calibration sensor's read; test_calibration_is_only_read checks it.
            text = text.replace(_protocol_function(READ_ONLY_FUNCTION, text), "")
        for found in _touches_calibration(text):
            problems.append(f"{path.relative_to(COMPONENT)}: {found}")
    assert not problems, "calibration data named in the integration:\n" + "\n".join(problems)


def _head_move_sites(word: str) -> list[str]:
    found = []
    for path in sorted(COMPONENT.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if word in line.split("#", 1)[0]:
                found.append(path.relative_to(COMPONENT).as_posix())
    return found


# The one function that may name calibration keys: it only reads them,
# for the diagnostic Calibration sensor.
READ_ONLY_FUNCTION = "_read_calibration_info"


def test_calibration_is_only_read() -> None:
    """The Calibration sensor's read sends GET requests and nothing else."""
    text = (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8")
    if f"async def {READ_ONLY_FUNCTION}(" not in text:
        pytest.skip("branch has no Calibration sensor")
    body = _protocol_function(READ_ONLY_FUNCTION)
    for forbidden in ("PUT", "POST", "set_config", "setUserConfigs", "_send", "upload", "file_type"):
        assert forbidden not in body, forbidden
    methods = re.findall(r'self\.request\(\s*[^,]+,\s*"([A-Z]+)"', body)
    assert methods, "no request found"
    assert set(methods) == {"GET"}, methods


def _protocol_function(name: str, text: str | None = None) -> str:
    if text is None:
        text = (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8")
    body = text[text.index(f"    async def {name}("):]
    end = body.find("\n    async def ", 10)
    return body if end < 0 else body[:end]


@pytest.mark.parametrize(
    ("word", "function"),
    [
        ("goTo", "focus_to"),         # Focus height and xtool.focus
        ("auto_start", "autofocus"),  # the Autofocus button
    ],
)
def test_head_moves_only_from_the_focus_controls(word: str, function: str) -> None:
    """Z moves only from the focus controls, which write no setting.

    Autofocus is Studio's ordinary Autofocus (the laser's own button does
    the same). Studio's calibration flows also send ``goTo``, but with
    ``autoHome`` and after writing calibration values.
    """
    found = _head_move_sites(word)
    for where in found:
        assert where == "protocols/ws_v2/protocol.py", f"head moved from {where}"
    if found:
        body = _protocol_function(function)
        assert word in body
        for forbidden in ("set_config", "/v1/device/configs", "setUserConfigs",
                          "/v1/laser-head/focus/parameter", "autoHome"):
            assert forbidden not in body, forbidden


def test_file_transfers_only_download() -> None:
    """The file channel is used to download (camera photos, calibration), never to upload a config file."""
    text = (COMPONENT / "protocols" / "ws_v2" / "protocol.py").read_text(encoding="utf-8")
    for match in re.finditer(r"file_type\s*=\s*3", text):
        call = text[max(0, match.start() - 200) : match.end()]
        assert "_download_file_stream(" in call, f"config file transfer that is not a download: {call.strip()}"


async def _press_everything(entity: Any) -> None:
    """Run every action a user or automation can take on a control."""
    actions: list[tuple[str, tuple]] = []
    if hasattr(entity, "async_turn_on"):
        actions += [("async_turn_on", ()), ("async_turn_off", ())]
    if hasattr(entity, "async_set_native_value"):
        low = getattr(entity, "native_min_value", 0) or 0
        high = getattr(entity, "native_max_value", 100) or 100
        actions += [("async_set_native_value", (low,)), ("async_set_native_value", (high,))]
    if hasattr(entity, "async_select_option"):
        for option in getattr(entity, "options", None) or []:
            actions.append(("async_select_option", (option,)))
    if hasattr(entity, "async_press"):
        actions.append(("async_press", ()))
    if hasattr(entity, "async_set_percentage"):
        actions += [("async_set_percentage", (0,)), ("async_set_percentage", (100,))]
    if hasattr(entity, "async_set_preset_mode"):
        for mode in getattr(entity, "preset_modes", None) or []:
            actions.append(("async_set_preset_mode", (mode,)))
    for name, args in actions:
        try:
            await getattr(entity, name)(*args)
        except Exception:  # noqa: BLE001 - only the requests sent matter here
            pass


@pytest.mark.parametrize("model_key", sorted(DEVICE_MODELS))
async def test_no_control_writes_calibration(
    hass: HomeAssistant, config_entry: MockConfigEntry, model_key: str
) -> None:
    """Every control of every model is used; none sends calibration data."""
    model = DEVICE_MODELS[model_key]
    coordinator = build_coordinator(hass, model)
    # A recording protocol: its async methods can be awaited, like the real one.
    protocol = MagicMock()
    protocol.host = "192.0.2.10"
    for name, member in inspect.getmembers(model.protocol_class):
        if not name.startswith("__") and inspect.iscoroutinefunction(member):
            setattr(protocol, name, AsyncMock(return_value={}))
    coordinator.protocol = protocol
    # Controls ask for a refresh after a write; it would leave a timer behind.
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_refresh = AsyncMock()

    controls: list[Any] = []
    for build in ("build_switches", "build_numbers", "build_selects", "build_lights", "build_buttons", "build_fans"):
        try:
            controls += getattr(coordinator, build)()
        except Exception:  # noqa: BLE001 - a model without that platform
            continue
    if not controls:
        pytest.skip(f"{model_key} has no controls")

    for entity in controls:
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()
        await _press_everything(entity)

    sent = [call for call in protocol.mock_calls if call[0]]
    problems = [
        f"{name}{args} {kwargs}: {found}"
        for name, args, kwargs in sent
        if (found := _touches_calibration(f"{name} {_words(args)} {_words(kwargs)}"))
    ]
    assert not problems, f"{model_key} sent calibration data:\n" + "\n".join(problems)
