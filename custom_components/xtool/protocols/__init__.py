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
from .ws_v2 import (
    WSV2_MODELS,
    WSV2Protocol,
    probe_v2,
)

_LOGGER = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    return text.upper().replace(" ", "").replace("-", "")


def _registry_key(model: XtoolDeviceModel) -> str:
    """Composite key so V1/V2 siblings of one model_id don't collide."""
    return f"{model.model_id}_{model.protocol_version}"


_ALL_MODELS: tuple[XtoolDeviceModel, ...] = (
    XTOOL_S1,
    XTOOL_D1, XTOOL_D1_PRO, XTOOL_D1_PRO_2_0,
    XTOOL_F1, XTOOL_F1_ULTRA, XTOOL_F1_ULTRA_V2, XTOOL_GS005,
    XTOOL_F2, XTOOL_F2_ULTRA, XTOOL_F2_ULTRA_SINGLE, XTOOL_F2_ULTRA_UV,
    XTOOL_M1, XTOOL_M1_ULTRA, XTOOL_METALFAB,
    XTOOL_P1, XTOOL_P2, XTOOL_P2S, XTOOL_P3,
    XTOOL_APPAREL_PRINTER,
    *WSV2_MODELS,
)


DEVICE_MODELS: dict[str, XtoolDeviceModel] = {
    _registry_key(m): m for m in _ALL_MODELS
}


def detect_models(device_name: str) -> list[XtoolDeviceModel]:
    """All registered models whose discovery_match (or model_id) appears in
    the discovered name. Sorted by match length descending so longer
    matches rank first within each protocol_version. Returns ``[]`` on
    no match.
    """
    name_norm = _normalize(device_name)
    hits: list[tuple[int, XtoolDeviceModel]] = []
    for model in _ALL_MODELS:
        patterns = model.discovery_match or (model.model_id,)
        for pattern in patterns:
            if not pattern:
                continue
            if _normalize(pattern) in name_norm:
                hits.append((len(_normalize(pattern)), model))
                break
    hits.sort(key=lambda h: h[0], reverse=True)
    return [model for _, model in hits]


def detect_model(device_name: str) -> XtoolDeviceModel:
    """Backwards-compat: first V1 candidate, else first candidate, else
    placeholder. Used by the options flow's "is this an S1?" check —
    callers that need V1/V2 disambiguation should use ``detect_models``.
    """
    candidates = detect_models(device_name)
    if not candidates:
        return XtoolDeviceModel(model_id="unknown", name=device_name)
    for model in candidates:
        if model.protocol_version == "V1":
            return model
    return candidates[0]


# Granular failure reasons surfaced by ``validate_connection``. Each is
# also a translation key in ``strings.json`` (``error`` + ``abort``
# blocks) so the config flow can render a precise hint.
VALIDATION_ERROR_UDP_NO_REPLY = "udp_no_reply"
VALIDATION_ERROR_UNKNOWN_MODEL = "unknown_model"
VALIDATION_ERROR_PROTOCOL_FAILED = "protocol_failed"


async def validate_connection(host: str) -> ConnectionInfo | str:
    """Identify a device by UDP probe, then validate via its protocol.

    Selection algorithm:

    1. UDP discovery → device name.
    2. ``candidates = detect_models(name)``  (1..N entries)
    3. Pick V2 candidate if probe_v2() succeeds; else V1; else V2-only.
    4. Connect with that candidate's protocol_class and confirm.

    Returns a populated :class:`ConnectionInfo` on success or one of the
    ``VALIDATION_ERROR_*`` translation keys when the flow should be
    aborted with a precise hint to the user.
    """
    discovered = await identify_host(host)
    if discovered is None or not discovered.name:
        _LOGGER.debug("No UDP reply from %s — cannot identify device", host)
        return VALIDATION_ERROR_UDP_NO_REPLY

    candidates = detect_models(discovered.name)
    if not candidates:
        _LOGGER.warning(
            "Unrecognised xTool device %r at %s — no matching model",
            discovered.name, host,
        )
        return VALIDATION_ERROR_UNKNOWN_MODEL

    v1_candidate = next(
        (m for m in candidates if m.protocol_version == "V1"), None
    )
    v2_candidate = next(
        (m for m in candidates if m.protocol_version == "V2"), None
    )

    chosen: XtoolDeviceModel | None = None
    # The UDP discovery already tells us "V1" vs "V2"; trust it and skip
    # the port-28900 TLS probe in that case. Fall back to the probe only
    # when the V1 leg of UDP answered (some V2 firmware revisions also
    # answer the legacy plain probe — keeping the fallback catches them).
    udp_says_v2 = (
        getattr(discovered, "protocol_version", "V1") == "V2"
    )
    if v2_candidate is not None and (udp_says_v2 or await probe_v2(host)):
        chosen = v2_candidate
        _LOGGER.info(
            "xTool %s at %s answered V2 probe — using V2 protocol",
            chosen.model_id, host,
        )
    elif v1_candidate is not None:
        chosen = v1_candidate
    elif v2_candidate is not None:
        # V2-only model (no V1 sibling). Probe failed but try anyway —
        # either it answers or the connect below fails cleanly.
        chosen = v2_candidate

    if chosen is None or chosen.protocol_class is None:
        _LOGGER.warning(
            "No usable protocol for xTool device %r at %s",
            discovered.name, host,
        )
        return VALIDATION_ERROR_UNKNOWN_MODEL

    protocol = chosen.protocol_class(host)
    try:
        await protocol.connect()
        info = await protocol.get_device_info()
        version = await protocol.get_version()
        firmware = info.main_firmware or version or ""
        return ConnectionInfo(
            host=host,
            name=info.device_name or chosen.name,
            serial_number=info.serial_number,
            firmware_version=firmware,
            laser_power_watts=info.laser_power_watts,
            device_info=info,
            protocol_version=chosen.protocol_version,
            model_id=chosen.model_id,
        )
    except Exception as err:
        _LOGGER.debug("Validation against %s as %s failed: %s",
                      host, chosen.model_id, err)
        return VALIDATION_ERROR_PROTOCOL_FAILED
    finally:
        await protocol.disconnect()


__all__ = [
    "ConnectionInfo",
    "DEVICE_MODELS",
    "DSeriesProtocol",
    "DeviceInfo",
    "LaserInfo",
    "RestProtocol",
    "S1Protocol",
    "WSV2Protocol",
    "XtoolDeviceModel",
    "XtoolDeviceState",
    "XtoolProtocol",
    "VALIDATION_ERROR_PROTOCOL_FAILED",
    "VALIDATION_ERROR_UDP_NO_REPLY",
    "VALIDATION_ERROR_UNKNOWN_MODEL",
    "detect_model",
    "detect_models",
    "validate_connection",
]
