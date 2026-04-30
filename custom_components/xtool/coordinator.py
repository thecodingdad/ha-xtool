"""DataUpdateCoordinator base class for xTool laser devices.

Each protocol family ships its own coordinator subclass in
``protocols/<family>/coordinator.py``. The subclass owns the polling
loop (``_async_update_data``), device-info fetch (``_fetch_device_info``),
all family-specific state (e.g. AP2, XCS, laser/wifi firmware versions,
workspace dims), and the ``build_<platform>()`` factories.

The base only carries cross-cutting state every family populates plus a
small set of generic helpers.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DEFAULT_AP2_POLL_INTERVAL,
    DEFAULT_DONGLE_POLL_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STATS_POLL_INTERVAL,
    DOMAIN,
    FIRMWARE_CHECK_INTERVAL,
    XtoolStatus,
)

# Import directly from protocols.base — pulling from the protocols package
# would eagerly load every family's __init__.py, which in turn loads each
# family's models.py → coordinator.py, creating an import cycle.
from .protocols.base import (
    LaserInfo,
    XtoolDeviceModel,
    XtoolDeviceState,
    XtoolProtocol,
)

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.components.button import ButtonEntity
    from homeassistant.components.camera import Camera
    from homeassistant.components.light import LightEntity
    from homeassistant.components.number import NumberEntity
    from homeassistant.components.select import SelectEntity
    from homeassistant.components.sensor import SensorEntity
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.components.update import UpdateEntity

_LOGGER = logging.getLogger(__name__)


class XtoolCoordinator(DataUpdateCoordinator[XtoolDeviceState]):
    """Base coordinator. One subclass per protocol family."""

    def __init__(
        self,
        hass: HomeAssistant,
        protocol: XtoolProtocol,
        device_name: str,
        serial_number: str,
        firmware_version: str,
        model: XtoolDeviceModel,
        power_switch_entity_id: str | None = None,
        enable_firmware_updates: bool = False,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        firmware_check_interval: int = FIRMWARE_CHECK_INTERVAL,
        ap2_poll_interval: int = DEFAULT_AP2_POLL_INTERVAL,
        stats_poll_interval: int = DEFAULT_STATS_POLL_INTERVAL,
        dongle_poll_interval: int = DEFAULT_DONGLE_POLL_INTERVAL,
        **_unused: Any,
    ) -> None:
        """Initialize the coordinator. Subclasses consume any extra kwargs."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{serial_number}",
            update_interval=timedelta(seconds=max(int(scan_interval), 1)),
        )
        self.host = protocol.host
        self.protocol = protocol
        self.device_name = device_name
        self.serial_number = serial_number
        self.firmware_version = firmware_version
        self.model = model
        self.laser = LaserInfo()
        self.power_switch_entity_id = power_switch_entity_id
        self.enable_firmware_updates = enable_firmware_updates
        self.firmware_check_interval = max(int(firmware_check_interval), 60)
        self.ap2_poll_interval = max(int(ap2_poll_interval), 1)
        self.stats_poll_interval = max(int(stats_poll_interval), 1)
        self.dongle_poll_interval = max(int(dongle_poll_interval), 1)
        self._device_info_fetched = False

        # mac_address is populated by S1/D-series/REST. F1 V2 leaves it empty.
        # entity.py reads it to add CONNECTION_NETWORK_MAC.
        self.mac_address: str = ""

    # --- Generic helpers ----------------------------------------------------

    @property
    def power_switch_is_off(self) -> bool:
        if not self.power_switch_entity_id:
            return False
        state = self.hass.states.get(self.power_switch_entity_id)
        return state is not None and state.state == "off"

    async def async_shutdown(self) -> None:
        await self.protocol.disconnect()
        await super().async_shutdown()

    def get_status(self) -> XtoolStatus:
        if self.data is None or self.data.status is None:
            return XtoolStatus.UNKNOWN
        return self.data.status

    # --- Polling (per-family override required by HA) -----------------------

    async def _async_update_data(self) -> XtoolDeviceState:
        raise NotImplementedError(
            f"{type(self).__name__} must implement _async_update_data"
        )

    # --- Entity factories (overridden per family) ---------------------------

    def build_switches(self) -> list["SwitchEntity"]:
        return []

    def build_numbers(self) -> list["NumberEntity"]:
        return []

    def build_buttons(self) -> list["ButtonEntity"]:
        return []

    def build_sensors(self) -> list["SensorEntity"]:
        return []

    def build_binary_sensors(self) -> list["BinarySensorEntity"]:
        return []

    def build_lights(self) -> list["LightEntity"]:
        return []

    def build_cameras(self) -> list["Camera"]:
        return []

    def build_selects(self) -> list["SelectEntity"]:
        return []

    def build_updates(self) -> list["UpdateEntity"]:
        return []
