"""M2 family entity factories — minimal v2.5.14 surface."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.camera import Camera
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.update import UpdateEntity
from homeassistant.const import UnitOfLength
from homeassistant.util import dt as dt_util

from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity, XtoolRestoringSensor
from ...sensor import XtoolSensor, XtoolSensorEntityDescription
from ...update import XtoolFirmwareUpdate

_LOGGER = logging.getLogger(__name__)

MIN_SNAPSHOT_INTERVAL = timedelta(seconds=2)


# --- Job control buttons ----------------------------------------------------


class M2PauseJob(XtoolEntity, ButtonEntity):
    _attr_translation_key = "pause_job"
    _attr_icon = "mdi:pause"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("pause_job")

    async def async_press(self) -> None:
        await self.coordinator.protocol.pause_job()
        await self.coordinator.async_request_refresh()


class M2ResumeJob(XtoolEntity, ButtonEntity):
    _attr_translation_key = "resume_job"
    _attr_icon = "mdi:play"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("resume_job")

    async def async_press(self) -> None:
        await self.coordinator.protocol.resume_job()
        await self.coordinator.async_request_refresh()


class M2CancelJob(XtoolEntity, ButtonEntity):
    _attr_translation_key = "cancel_job"
    _attr_icon = "mdi:stop"

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("cancel_job")

    async def async_press(self) -> None:
        await self.coordinator.protocol.cancel_job()
        await self.coordinator.async_request_refresh()


# --- Sensors ----------------------------------------------------------------


SENSOR_DESCRIPTIONS: tuple[XtoolSensorEntityDescription, ...] = (
    XtoolSensorEntityDescription(
        key="task_id",
        translation_key="task_id",
        icon="mdi:identifier",
        entity_registry_enabled_default=False,
        value_fn=lambda state, _: state.task_id or None,
    ),
    XtoolSensorEntityDescription(
        key="laser_position_x",
        translation_key="laser_position_x",
        icon="mdi:axis-x-arrow",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=2,
        value_fn=lambda state, _: state.position_x,
    ),
    XtoolSensorEntityDescription(
        key="laser_position_y",
        translation_key="laser_position_y",
        icon="mdi:axis-y-arrow",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=2,
        value_fn=lambda state, _: state.position_y,
    ),
)


# --- Cover binary sensor ----------------------------------------------------


class M2CoverSensor(XtoolEntity, BinarySensorEntity):
    _attr_translation_key = "cover_open"
    _attr_icon = "mdi:lid"
    _attr_device_class = BinarySensorDeviceClass.OPENING

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._set_unique_id("cover_open")

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        if d is None:
            return None
        return d.cover_open


# --- Camera -----------------------------------------------------------------


class M2Camera(XtoolEntity, Camera):
    """xTool M2 camera — JPEG snapshot via /v1/platform/camera/snap.

    Live h264-raw / mpeg2-ts WS stream on
    ``ws://<ip>:8089/v1/wsplayer?stream=<id>`` is deferred until a
    real M2 capture confirms the wire shape. Snapshot-only for now,
    matching the WS-V2 family pattern.
    """

    _attr_is_streaming = False

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        key: str,
        camera_name: str,
    ) -> None:
        XtoolEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._set_unique_id(key)
        self._attr_translation_key = key
        self._attr_icon = "mdi:camera"
        self._camera_name = camera_name
        self._last_snapshot: bytes | None = None
        self._last_snapshot_time = dt_util.utcnow() - MIN_SNAPSHOT_INTERVAL

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None,
    ) -> bytes | None:
        now = dt_util.utcnow()
        if (
            self._last_snapshot is not None
            and now - self._last_snapshot_time < MIN_SNAPSHOT_INTERVAL
        ):
            return self._last_snapshot
        try:
            image = await self.coordinator.protocol.camera_snap(self._camera_name)
        except Exception as err:
            _LOGGER.debug("M2 camera %s snap failed: %s", self._camera_name, err)
            return self._last_snapshot
        if image:
            self._last_snapshot = image
            self._last_snapshot_time = now
        return image or self._last_snapshot


# --- Firmware update --------------------------------------------------------


class M2FirmwareUpdate(XtoolFirmwareUpdate):
    """xTool M2 firmware update — single cloud package (multi-board
    bundled internally per the version.json manifest)."""


# --- Builders ---------------------------------------------------------------


def build_m2_buttons(coordinator: XtoolCoordinator) -> list[ButtonEntity]:
    return [
        M2PauseJob(coordinator),
        M2ResumeJob(coordinator),
        M2CancelJob(coordinator),
    ]


def build_m2_sensors(coordinator: XtoolCoordinator) -> list[SensorEntity]:
    return [
        XtoolSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]


def build_m2_binary_sensors(coordinator: XtoolCoordinator) -> list[BinarySensorEntity]:
    out: list[BinarySensorEntity] = []
    if coordinator.model.has_cover_sensor:
        out.append(M2CoverSensor(coordinator))
    return out


def build_m2_cameras(coordinator: XtoolCoordinator) -> list[Camera]:
    if not coordinator.model.has_camera:
        return []
    names = coordinator.model.camera_names or ("main",)
    return [
        M2Camera(coordinator, f"camera_{name}", name) for name in names
    ]


def build_m2_updates(coordinator: XtoolCoordinator) -> list[UpdateEntity]:
    if coordinator.model.firmware_content_id:
        return [M2FirmwareUpdate(coordinator)]
    return []
