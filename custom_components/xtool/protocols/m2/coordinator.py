"""M2 family coordinator — extends WSV2Coordinator with M2 entity builders."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..ws_v2.coordinator import WSV2Coordinator

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.components.button import ButtonEntity
    from homeassistant.components.camera import Camera
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.update import UpdateEntity

_LOGGER = logging.getLogger(__name__)


class M2Coordinator(WSV2Coordinator):
    """Coordinator for xTool M2.

    Inherits the V2 transport + push-drain pipeline + accessory
    subsystem from :class:`WSV2Coordinator`. Only the entity
    builders are overridden so the M2-specific Status / Camera /
    Buttons surface lands instead of the F-family entity set.
    """

    def build_buttons(self) -> list["ButtonEntity"]:
        from .entities import build_m2_buttons
        return build_m2_buttons(self)

    def build_sensors(self) -> list["SensorEntity"]:
        from .entities import build_m2_sensors
        return build_m2_sensors(self)

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        from .entities import build_m2_binary_sensors
        return build_m2_binary_sensors(self)

    def build_cameras(self) -> list["Camera"]:
        from .entities import build_m2_cameras
        return build_m2_cameras(self)

    def build_updates(self) -> list["UpdateEntity"]:
        from .entities import build_m2_updates
        return build_m2_updates(self)
