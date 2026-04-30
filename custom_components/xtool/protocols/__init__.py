"""xTool protocol families and device registry."""

from __future__ import annotations

import logging

from ..discovery import identify_host
from .base import (
    ConnectionInfo,
    DeviceInfo,
    LaserInfo,
    XtoolDeviceModel,
    XtoolDeviceState,
    XtoolProtocol,
)
from .d_series import (
    DSeriesProtocol,
    XTOOL_D1,
    XTOOL_D1_PRO,
    XTOOL_D1_PRO_2_0,
)
from .f1v2 import F1V2Protocol, XTOOL_F1_V2
from .rest import (
    RestProtocol,
    XTOOL_APPAREL_PRINTER,
    XTOOL_F1,
    XTOOL_F1_ULTRA,
    XTOOL_F1_ULTRA_V2,
    XTOOL_F2,
    XTOOL_F2_ULTRA,
    XTOOL_F2_ULTRA_SINGLE,
    XTOOL_F2_ULTRA_UV,
    XTOOL_GS005,
    XTOOL_M1,
    XTOOL_M1_ULTRA,
    XTOOL_METALFAB,
    XTOOL_P1,
    XTOOL_P2,
    XTOOL_P2S,
    XTOOL_P3,
)
from .s1 import S1Protocol, XTOOL_S1

_LOGGER = logging.getLogger(__name__)

DEVICE_MODELS: dict[str, XtoolDeviceModel] = {
    m.model_id: m
    for m in (
        XTOOL_S1,
        XTOOL_D1, XTOOL_D1_PRO, XTOOL_D1_PRO_2_0,
        XTOOL_F1, XTOOL_F1_ULTRA, XTOOL_F1_ULTRA_V2, XTOOL_F1_V2, XTOOL_GS005,
        XTOOL_F2, XTOOL_F2_ULTRA, XTOOL_F2_ULTRA_SINGLE, XTOOL_F2_ULTRA_UV,
        XTOOL_M1, XTOOL_M1_ULTRA, XTOOL_METALFAB,
        XTOOL_P1, XTOOL_P2, XTOOL_P2S, XTOOL_P3,
        XTOOL_APPAREL_PRINTER,
    )
}


def detect_model(device_name: str) -> XtoolDeviceModel:
    """Match a reported device name to a known model spec.

    Iterates models sorted by ``model_id`` length descending so the longest
    substring wins — without this, ``"xTool F1 Ultra"`` would match the
    plain ``F1`` entry before the more specific ``F1Ultra`` one. Returns a
    placeholder unknown model (no protocol_class) on miss so the caller can
    present a clear error.
    """
    name_upper = device_name.upper().replace(" ", "").replace("-", "")
    candidates = sorted(
        DEVICE_MODELS.values(),
        key=lambda m: len(m.model_id),
        reverse=True,
    )
    for model in candidates:
        if model.model_id.upper().replace(" ", "").replace("-", "") in name_upper:
            return model
    return XtoolDeviceModel(model_id="unknown", name=device_name)


async def validate_connection(host: str) -> ConnectionInfo | None:
    """Identify a device by UDP probe, then validate via its protocol."""
    discovered = await identify_host(host)
    if discovered is None or not discovered.name:
        _LOGGER.debug("No UDP reply from %s — cannot identify device", host)
        return None

    model = detect_model(discovered.name)
    if model.protocol_class is None:
        _LOGGER.warning(
            "Unrecognised xTool device %r at %s — no matching protocol",
            discovered.name, host,
        )
        return None

    protocol = model.protocol_class(host)
    try:
        await protocol.connect()
        info = await protocol.get_device_info()
        version = await protocol.get_version()
        # Prefer the main MCU firmware reported via M-code/JSON; fall back to
        # whatever get_version() yielded.
        firmware = info.main_firmware or version or ""
        return ConnectionInfo(
            host=host,
            name=info.device_name or model.name,
            serial_number=info.serial_number,
            firmware_version=firmware,
            laser_power_watts=info.laser_power_watts,
            device_info=info,
        )
    except Exception as err:
        _LOGGER.debug("Validation against %s as %s failed: %s",
                      host, model.model_id, err)
        return None
    finally:
        await protocol.disconnect()


__all__ = [
    "ConnectionInfo",
    "DEVICE_MODELS",
    "DSeriesProtocol",
    "DeviceInfo",
    "F1V2Protocol",
    "LaserInfo",
    "RestProtocol",
    "S1Protocol",
    "XtoolDeviceModel",
    "XtoolDeviceState",
    "XtoolProtocol",
    "detect_model",
    "validate_connection",
]
