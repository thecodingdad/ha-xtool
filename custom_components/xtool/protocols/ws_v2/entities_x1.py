"""Observed X1 diagnostics and snapshot-only cameras, without controls."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.camera import Camera
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from ...entity import XtoolEntity

if TYPE_CHECKING:
    from .coordinator_x1 import X1DiscoveryCoordinator, X1ReadOnlyCoordinator


class X1IdentitySensor(XtoolEntity, SensorEntity):
    """Read one advertised identity field while discovery is fresh."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    coordinator: X1DiscoveryCoordinator

    def __init__(
        self, coordinator: X1DiscoveryCoordinator, key: str, name: str
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._set_unique_id("discovery_" + key)

    @property
    def native_value(self) -> str | None:
        device = self.coordinator.protocol.discovery
        if device is None:
            return None
        if self._key == "device_name":
            return device.name
        if self._key == "firmware_version":
            return device.version
        if self._key == "device_code":
            return device.device_code
        return device.platform_version


class X1DiscoveryReachable(XtoolEntity, BinarySensorEntity):
    """An advertisement response is not proof that the machine is idle/safe."""

    _attr_name = "Discovery reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: X1DiscoveryCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("discovery_reachable")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return bool(
            self.coordinator.last_update_success
            and self.coordinator.data
            and self.coordinator.data.available
        )


class X1RawStateSensor(XtoolEntity, SensorEntity):
    """An observed string, not a normalized operating or safety assertion."""

    coordinator: X1ReadOnlyCoordinator

    def __init__(self, coordinator: X1ReadOnlyCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._set_unique_id("reported_" + key)

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.raw_states.get(self._key)


class X1ModuleSensor(XtoolEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    coordinator: X1ReadOnlyCoordinator

    def __init__(
        self, coordinator: X1ReadOnlyCoordinator, module_id: int, key: str
    ) -> None:
        super().__init__(coordinator)
        self._module_id = module_id
        self._key = key
        label = "Dongle" if module_id == 71 else f"Module {module_id}"
        self._attr_name = (
            f"{label} {'reported status' if key == 'common_status' else 'firmware'}"
        )
        self._set_unique_id(f"module_{module_id}_{key}")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        module = self.coordinator.data.modules.get(self._module_id)
        if module is None or not module.online:
            return None
        return (
            module.common_status if self._key == "common_status" else module.app_version
        )


class X1ModuleOnline(XtoolEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    coordinator: X1ReadOnlyCoordinator

    def __init__(self, coordinator: X1ReadOnlyCoordinator, module_id: int) -> None:
        super().__init__(coordinator)
        self._module_id = module_id
        label = "Dongle" if module_id == 71 else f"Module {module_id}"
        self._attr_name = f"{label} connected"
        self._set_unique_id(f"module_{module_id}_connected")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        module = self.coordinator.data.modules.get(self._module_id)
        return module.online if module is not None else None


class X1BluetoothInventorySensor(XtoolEntity, SensorEntity):
    """Connected-only query, without guessed type-specific telemetry."""

    _attr_name = "Connected Bluetooth accessories"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    coordinator: X1ReadOnlyCoordinator

    def __init__(self, coordinator: X1ReadOnlyCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("bluetooth_connected_count")

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None or self.coordinator.data.bluetooth is None:
            return None
        return sum(device.connected for device in self.coordinator.data.bluetooth)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        devices = (
            self.coordinator.data.bluetooth
            if self.coordinator.data is not None
            else None
        )
        return {
            "reported_type_prefixes": sorted({item.type_prefix for item in devices})
            if devices is not None
            else None,
            "per_accessory_telemetry": "unverified",
        }


class X1SnapshotCamera(XtoolEntity, Camera):
    """On-demand JPEG only. No streaming subscription or camera controls."""

    coordinator: X1ReadOnlyCoordinator
    _attr_content_type = "image/jpeg"

    def __init__(self, coordinator: X1ReadOnlyCoordinator, name: str) -> None:
        XtoolEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._camera_name = name
        self._attr_name = f"{name.capitalize()} snapshot"
        self._set_unique_id(f"snapshot_{name}")

    @property
    def available(self) -> bool:
        return bool(
            super().available
            and self.coordinator.data is not None
            and self._camera_name in self.coordinator.data.cameras
        )

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes:
        from homeassistant.exceptions import HomeAssistantError

        if not self.available:
            raise HomeAssistantError("X1 snapshot camera unavailable")
        return await self.coordinator.async_snapshot(self._camera_name)

    async def handle_async_mjpeg_stream(self, request):
        from aiohttp.web import HTTPNotImplemented

        raise HTTPNotImplemented(text="X1 live streaming is not supported")
