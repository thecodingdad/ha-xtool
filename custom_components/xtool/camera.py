"""Camera entities for xTool Laser integration (P2/P2S models)."""

from __future__ import annotations

from datetime import timedelta
import logging

import aiohttp

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import XtoolConfigEntry
from .const import DEFAULT_HTTP_PORT
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity

_LOGGER = logging.getLogger(__name__)

MIN_SNAPSHOT_INTERVAL = timedelta(seconds=30)

CAMERAS = [
    {"index": 0, "key": "camera_overview", "translation_key": "camera_overview"},
    {"index": 1, "key": "camera_closeup", "translation_key": "camera_closeup"},
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool camera entities."""
    coordinator = entry.runtime_data
    if not coordinator.model.has_camera:
        return
    async_add_entities(
        XtoolCamera(coordinator, cam["index"], cam["key"], cam["translation_key"])
        for cam in CAMERAS
    )


class XtoolCamera(XtoolEntity, Camera):
    """Representation of an xTool camera (P2/P2S overview or close-up)."""

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        stream_index: int,
        key: str,
        translation_key: str,
    ) -> None:
        """Initialize the camera."""
        XtoolEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._stream_index = stream_index
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{coordinator.serial_number}_{key}"
        self._last_image: bytes | None = None
        self._last_fetch = dt_util.utcnow() - MIN_SNAPSHOT_INTERVAL

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        now = dt_util.utcnow()
        if self._last_image and (now - self._last_fetch) < MIN_SNAPSHOT_INTERVAL:
            return self._last_image

        url = f"http://{self.coordinator.host}:{DEFAULT_HTTP_PORT}/camera/snap?stream={self._stream_index}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        self._last_image = await resp.read()
                        self._last_fetch = now
                        return self._last_image
        except Exception as err:
            _LOGGER.debug("Camera snapshot failed: %s", err)
        return self._last_image
