"""Sensor entities for xTool Laser integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .const import M222_STATUS_MAP, SECONDS_PER_HOUR, XtoolStatus
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity
from .models import XtoolDeviceState


@dataclass(frozen=True, kw_only=True)
class XtoolSensorEntityDescription(SensorEntityDescription):
    """Describes an xTool sensor entity."""

    value_fn: Callable[[XtoolDeviceState, XtoolCoordinator], str | int | float | None]


SENSOR_DESCRIPTIONS: tuple[XtoolSensorEntityDescription, ...] = (
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
    XtoolSensorEntityDescription(
        key="laser_position_z",
        translation_key="laser_position_z",
        icon="mdi:axis-z-arrow",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=2,
        value_fn=lambda state, _: state.position_z,
    ),
    XtoolSensorEntityDescription(
        key="fire_level",
        translation_key="fire_level",
        icon="mdi:fire",
        value_fn=lambda state, _: state.fire_level,
    ),
    XtoolSensorEntityDescription(
        key="air_assist_level",
        translation_key="air_assist_level",
        icon="mdi:weather-windy",
        value_fn=lambda state, _: state.air_assist_level,
    ),
    XtoolSensorEntityDescription(
        key="task_id",
        translation_key="task_id",
        icon="mdi:identifier",
        value_fn=lambda state, _: state.task_id or None,
    ),
    XtoolSensorEntityDescription(
        key="task_time",
        translation_key="task_time",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        value_fn=lambda state, _: state.task_time,
    ),
    XtoolSensorEntityDescription(
        key="working_time",
        translation_key="working_time",
        icon="mdi:clock-outline",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda state, _: round(state.working_seconds / SECONDS_PER_HOUR, 1) if state.working_seconds is not None else None,
    ),
    XtoolSensorEntityDescription(
        key="session_count",
        translation_key="session_count",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda state, _: state.session_count,
    ),
    XtoolSensorEntityDescription(
        key="standby_time",
        translation_key="standby_time",
        icon="mdi:sleep",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda state, _: round(state.standby_seconds / SECONDS_PER_HOUR, 1) if state.standby_seconds is not None else None,
    ),
    XtoolSensorEntityDescription(
        key="tool_runtime",
        translation_key="tool_runtime",
        icon="mdi:laser-pointer",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda state, _: round(state.tool_runtime_seconds / SECONDS_PER_HOUR, 1) if state.tool_runtime_seconds is not None else None,
    ),
    XtoolSensorEntityDescription(
        key="ip_address",
        translation_key="ip_address",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _, coord: coord.host,
    ),
    XtoolSensorEntityDescription(
        key="laser_power",
        translation_key="laser_power",
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="W",
        value_fn=lambda state, coord: coord.laser.power_watts or None,
    ),
    XtoolSensorEntityDescription(
        key="laser_module",
        translation_key="laser_module",
        icon="mdi:laser-pointer",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state, coord: coord.laser.description if coord.laser.power_watts else None,
    ),
    XtoolSensorEntityDescription(
        key="sd_card",
        translation_key="sd_card",
        icon="mdi:sd",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=["inserted", "not_inserted"],
        value_fn=lambda state, _: "inserted" if state.sd_card_present else "not_inserted",
    ),
    XtoolSensorEntityDescription(
        key="workspace_size",
        translation_key="workspace_size",
        icon="mdi:ruler-square",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _, coord: (
            f"{coord.workspace_x:.0f} × {coord.workspace_y:.0f} × {coord.workspace_z:.0f} mm"
            if coord.workspace_x else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool sensor entities."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [XtoolStatusSensor(coordinator)]
    entities.extend(
        XtoolSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )
    async_add_entities(entities)


class XtoolSensor(XtoolEntity, SensorEntity):
    """Representation of an xTool sensor."""

    entity_description: XtoolSensorEntityDescription

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        description: XtoolSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.serial_number}_{description.key}"

    @property
    def native_value(self) -> str | int | float | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data, self.coordinator)


class XtoolStatusSensor(XtoolEntity, SensorEntity):
    """Status sensor that shows 'off' when device is unreachable instead of 'unavailable'."""

    _attr_translation_key = "status"
    _attr_icon = "mdi:laser-pointer"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [s.value for s in XtoolStatus]

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        """Initialize the status sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_status"
        self._last_known_status: str = XtoolStatus.OFF

    @property
    def available(self) -> bool:
        """Always available — shows 'off' when device is unreachable."""
        return True

    @property
    def native_value(self) -> str:
        """Return the device status.

        Shows "off" when device is unreachable or power switch is off.
        Keeps last known status when the status query fails transiently.
        """
        if self.coordinator.power_switch_is_off:
            return XtoolStatus.OFF
        if self.coordinator.data is None or not self.coordinator.data.available:
            return XtoolStatus.OFF
        status = M222_STATUS_MAP.get(self.coordinator.data.status_code)
        if status is not None:
            self._last_known_status = status.value
            return status.value
        # status_code not in map (e.g. -1 from empty response) → keep last known
        return self._last_known_status
