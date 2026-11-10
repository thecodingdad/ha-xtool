"""Isolated X1 discovery and authenticated read-only coordinators."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from ...coordinator import XtoolCoordinator
from ..base import XtoolDeviceState
from .protocol_x1 import X1DiscoveryProtocol
from .x1_auth import X1AuthError
from .x1_data import X1ProtocolError, X1ReadOnlyState
from .x1_transport import X1ReadOnlyProtocol

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.components.sensor import SensorEntity


class X1DiscoveryCoordinator(XtoolCoordinator):
    """Publish only observed advertisement identity and reachability."""

    protocol: X1DiscoveryProtocol

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        protocol: X1DiscoveryProtocol,
        **kwargs: Any,
    ) -> None:
        kwargs["power_switch_entity_id"] = None
        kwargs["enable_firmware_updates"] = False
        kwargs["scan_interval"] = max(int(kwargs.get("scan_interval", 60)), 60)
        super().__init__(hass, protocol=protocol, **kwargs)
        # DataUpdateCoordinator includes its name in outage logs; omit identifiers.
        self.name = "xtool_x1_discovery"
        if self.serial_number:
            protocol.bind_serial(self.serial_number)

    async def _async_update_data(self) -> XtoolDeviceState:
        state = XtoolDeviceState()
        try:
            await self.protocol.poll_state(state)
        except (ConnectionError, OSError, TimeoutError) as err:
            raise UpdateFailed("X1 discovery unavailable or identity rejected") from err
        self.device_name = state.device_name
        self.serial_number = state.serial_number
        self.firmware_version = state.firmware_version
        return state

    def build_sensors(self) -> list[SensorEntity]:
        from .entities_x1 import X1IdentitySensor

        return [
            X1IdentitySensor(self, key, name)
            for key, name in (
                ("device_name", "Advertised name"),
                ("firmware_version", "Advertised firmware"),
                ("device_code", "Advertised model code"),
                ("platform_version", "Advertised platform"),
            )
        ]

    def build_binary_sensors(self) -> list[BinarySensorEntity]:
        from .entities_x1 import X1DiscoveryReachable

        return [X1DiscoveryReachable(self)]


class X1ReadOnlyCoordinator(XtoolCoordinator):
    """Only authenticated observations; never legacy status or accessory controls."""

    protocol: X1ReadOnlyProtocol
    data: X1ReadOnlyState

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        protocol: X1ReadOnlyProtocol,
        **kwargs: Any,
    ) -> None:
        kwargs["enable_firmware_updates"] = False
        kwargs["power_switch_entity_id"] = None
        kwargs["scan_interval"] = max(int(kwargs.get("scan_interval", 60)), 60)
        super().__init__(hass, protocol=protocol, **kwargs)
        self.name = "xtool_x1_readonly"
        self._module_entities: dict[str, set[int]] = {
            "sensor": set(),
            "binary_sensor": set(),
        }

    async def _async_update_data(self) -> X1ReadOnlyState:
        try:
            state = await self.protocol.poll_readonly()
        except X1AuthError:
            raise ConfigEntryAuthFailed(
                "X1 authorization or binding refused; owner review required"
            ) from None
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError):
            raise UpdateFailed("X1 read-only connection or response failed") from None
        self.firmware_version = state.firmware_version
        state.device_name = self.device_name
        for platform in ("sensor", "binary_sensor"):
            add = self._platform_callbacks.get(platform)
            if add is not None:
                entities = self._new_module_entities(platform, state)
                if entities:
                    add(entities)
        return state

    def _new_module_entities(self, platform: str, state: X1ReadOnlyState) -> list:
        from .entities_x1 import X1ModuleOnline, X1ModuleSensor

        entities = []
        for module_id in state.modules.keys() - self._module_entities[platform]:
            if platform == "sensor":
                entities.extend(
                    [
                        X1ModuleSensor(self, module_id, "common_status"),
                        X1ModuleSensor(self, module_id, "app_version"),
                    ]
                )
            else:
                entities.append(X1ModuleOnline(self, module_id))
            self._module_entities[platform].add(module_id)
        return entities

    def build_sensors(self) -> list[SensorEntity]:
        from .entities_x1 import X1BluetoothInventorySensor, X1RawStateSensor

        sensors: list[SensorEntity] = [
            X1RawStateSensor(self, key, name)
            for key, name in (
                ("mode", "Reported mode"),
                ("moveState", "Reported movement state"),
                ("gohomeState", "Reported homing state"),
            )
        ]
        sensors.append(X1BluetoothInventorySensor(self))
        if self.data is not None:
            sensors.extend(self._new_module_entities("sensor", self.data))
        return sensors

    def build_binary_sensors(self) -> list[BinarySensorEntity]:
        if self.data is None:
            return []
        return self._new_module_entities("binary_sensor", self.data)

    def build_cameras(self) -> list:
        from .entities_x1 import X1SnapshotCamera

        if self.data is None:
            return []
        return [
            X1SnapshotCamera(self, name)
            for name in ("far", "near", "side")
            if name in self.data.cameras
        ]

    async def async_snapshot(self, name: str) -> bytes:
        from homeassistant.exceptions import HomeAssistantError

        try:
            return await self.protocol.snapshot(name)
        except (
            X1AuthError,
            X1ProtocolError,
            OSError,
            TimeoutError,
            aiohttp.ClientError,
        ):
            self.async_set_update_error(UpdateFailed("X1 snapshot failed"))
            raise HomeAssistantError("X1 snapshot failed; no image returned") from None
