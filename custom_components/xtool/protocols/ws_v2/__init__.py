"""xTool V2 protocol family — TLS WebSocket-tunneled REST API on port 28900.

Used by F1 / F1 Ultra / F1 Ultra V2 / F1 Lite / F2 family / M1 Ultra / P2S /
P3 / MetalFab / Apparel Printer when their firmware is recent enough to
expose the ``/v1/...`` API surface (xTool Studio / xTool Creative Space
"V2" protocol). Older firmware on the same hardware keeps using the
legacy REST family on port 8080.
"""

from __future__ import annotations

from .models import (
    WSV2_MODELS,
    XTOOL_APPAREL_PRINTER_WSV2,
    XTOOL_F1_LITE_WSV2,
    XTOOL_F1_ULTRA_V2_WSV2,
    XTOOL_F1_ULTRA_WSV2,
    XTOOL_F1_WSV2,
    XTOOL_F2_ULTRA_SINGLE_WSV2,
    XTOOL_F2_ULTRA_UV_WSV2,
    XTOOL_F2_ULTRA_WSV2,
    XTOOL_F2_WSV2,
    XTOOL_M1_ULTRA_WSV2,
    XTOOL_METALFAB_WSV2,
    XTOOL_P2S_WSV2,
    XTOOL_P3_WSV2,
)
from .protocol import (
    WSV2_HEARTBEAT_SECONDS,
    WSV2_PROBE_TIMEOUT,
    WSV2_PATH,
    WSV2_PORT,
    WSV2Protocol,
    probe_v2,
)

__all__ = [
    "WSV2_HEARTBEAT_SECONDS",
    "WSV2_MODELS",
    "WSV2_PROBE_TIMEOUT",
    "WSV2_PATH",
    "WSV2_PORT",
    "WSV2Protocol",
    "XTOOL_APPAREL_PRINTER_WSV2",
    "XTOOL_F1_LITE_WSV2",
    "XTOOL_F1_ULTRA_V2_WSV2",
    "XTOOL_F1_ULTRA_WSV2",
    "XTOOL_F1_WSV2",
    "XTOOL_F2_ULTRA_SINGLE_WSV2",
    "XTOOL_F2_ULTRA_UV_WSV2",
    "XTOOL_F2_ULTRA_WSV2",
    "XTOOL_F2_WSV2",
    "XTOOL_M1_ULTRA_WSV2",
    "XTOOL_METALFAB_WSV2",
    "XTOOL_P2S_WSV2",
    "XTOOL_P3_WSV2",
    "probe_v2",
]
