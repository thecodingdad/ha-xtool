"""xTool M2 — WS-V2 multi-channel WebSocket with M2-specific URL set.

M2 (model_id JS002) is classified by Studio as a V2-protocol
device: TLS WebSocket on port 28900 with the existing V2
multi-channel framework, but with a new URL surface
(``/v1/platform/*`` + ``/v1/project/*``) replacing the
F-family's ``/v1/device/*`` + ``/v1/peripheral/*`` umbrellas.

The protocol class extends :class:`WSV2Protocol` so the
transport, push-drain pipeline, BT accessory subsystem,
file_stream, and OTA helpers all stay shared. Only the
URL-specific endpoint mapping is overridden.
"""

from __future__ import annotations

from .models import XTOOL_M2
from .protocol import M2WSV2Protocol

__all__ = [
    "M2WSV2Protocol",
    "XTOOL_M2",
]
