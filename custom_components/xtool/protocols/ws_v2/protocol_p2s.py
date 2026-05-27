"""WS-V2 protocol dialect for the xTool P2S.

P2S V2 firmware (ZY013 / `xcs-ext-p2s` bundle) classifies itself
``protocolVersion:"V2"`` in Studio's manifest, rides the same TLS
WebSocket transport on port 28900 and the same multi-channel framework
(instruction / file_stream / media_stream) as the F1 / F2 family, but
diverges on four routing keys:

- Status — ``/v1/device/runningStatus`` (not ``/v1/device/runtime-infos``)
- Configs read — ``GET /v1/config/get`` with ``data:{alias,type,kv:[…]}``
  (not ``GET /v1/device/configs``)
- Configs write — ``PUT /v1/config/set`` (not ``PUT /v1/device/configs``)
- Camera snap — ``GET /v1/camera/image`` with ``data:{stream:"0" | "1"}``
  (not ``GET /v1/camera/snap`` with ``params:{name}``)
- Statistics — ``GET /v1/device/workingInfo`` (not ``/v1/device/statistics``)

Response shapes match the F1/F2 norm closely enough that the base
``WSV2Protocol`` poll-state machinery (``_apply_configs``,
``_apply_latest_to_state``, push-drain pipeline) just works once the
URLs are swapped.
"""

from __future__ import annotations

import logging
from typing import Any

from ..base import XtoolDeviceState
from .protocol import WSV2Protocol

_LOGGER = logging.getLogger(__name__)


class P2SWSV2Protocol(WSV2Protocol):
    """WS-V2 with the P2S URL set."""

    PATH_RUNTIME_INFOS = "/v1/device/runningStatus"
    PATH_CONFIGS_GET = "/v1/config/get"
    PATH_CONFIGS_SET = "/v1/config/set"
    PATH_STATISTICS = "/v1/device/workingInfo"
    PATH_CAMERA_SNAP = "/v1/camera/image"

    # Keys we want surfaced from the persistent config blob. P2S's
    # ``/v1/config/get`` requires the caller to list which keys to
    # fetch (unlike the F1/F2 endpoint which dumps the whole blob).
    # Kept in sync with ``_apply_configs`` in the base class.
    _CONFIG_KEYS_TO_FETCH = (
        "flameAlarm", "beepEnable", "gapCheck", "machineLockCheck",
        "autoSleepEnable", "fillLightBrightFront", "fillLightBrightBack",
        "purifierTimeout", "workingMode", "airAssistDelay",
        "airassistCut", "airassistGrave",
        "sleepTimeout", "sleepTimeoutOpenGap", "printToolType",
    )

    async def _poll_configs(self) -> None:
        """P2S V2 reads configs via ``/v1/config/get`` with a key list.

        Body shape mirrors Studio's ``queryCurrentTaskId`` route:
        ``{alias:"config", type:"user", kv:["key1", "key2", …]}``.
        Response carries the requested keys as a flat dict — base
        ``_apply_configs`` walks either shape.
        """
        try:
            cfg = await self.request(
                self.PATH_CONFIGS_GET, "GET",
                data={
                    "alias": "config",
                    "type": "user",
                    "kv": list(self._CONFIG_KEYS_TO_FETCH),
                },
            )
        except Exception as err:
            _LOGGER.debug("P2S V2 %s failed: %s", self.PATH_CONFIGS_GET, err)
            return
        if isinstance(cfg, dict):
            self._apply_configs(cfg)

    async def camera_snap(self, camera_name: str = "overview") -> bytes | None:
        """P2S camera-snap uses ``/v1/camera/image`` with ``data:{stream}``.

        Maps the model's friendly ``camera_names`` ("overview" /
        "closeup") to the firmware-canonical stream indices ("0" /
        "1"). The downstream blob transfer flows through
        ``file_stream`` exactly like the F1/F2 snap path.
        """
        stream = "1" if camera_name == "closeup" else "0"
        try:
            snap = await self.request(
                self.PATH_CAMERA_SNAP, "GET",
                data={"stream": stream},
                timeout=15.0,
            )
        except Exception as err:
            _LOGGER.debug("P2S camera_snap %s failed: %s", camera_name, err)
            return None
        if not isinstance(snap, dict):
            return None
        filename = snap.get("filename")
        if not isinstance(filename, str) or not filename:
            _LOGGER.debug(
                "P2S camera_snap %s: no filename in response %s",
                camera_name, snap,
            )
            return None
        try:
            blob = await self._download_file_stream(filename, file_type=5)
        except Exception as err:
            _LOGGER.debug(
                "P2S camera_snap %s: file_stream download failed: %s",
                camera_name, err,
            )
            return None
        try:
            await self.request(
                "/v1/filetransfer/finish",
                "PUT",
                data={"filename": filename},
            )
        except Exception:
            pass
        return blob if blob else None
