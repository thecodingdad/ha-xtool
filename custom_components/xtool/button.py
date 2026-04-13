"""Button entities for xTool Laser integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .const import (
    CMD_CANCEL_JOB,
    CMD_HOME_ALL,
    CMD_HOME_XY,
    CMD_HOME_Z,
    CMD_PAUSE_JOB,
    CMD_RESUME_JOB,
)
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity


@dataclass(frozen=True, kw_only=True)
class XtoolButtonEntityDescription(ButtonEntityDescription):
    """Describes an xTool button entity."""

    command: str


BUTTON_DESCRIPTIONS: tuple[XtoolButtonEntityDescription, ...] = (
    XtoolButtonEntityDescription(
        key="pause_job",
        translation_key="pause_job",
        icon="mdi:pause",
        command=CMD_PAUSE_JOB,
    ),
    XtoolButtonEntityDescription(
        key="resume_job",
        translation_key="resume_job",
        icon="mdi:play",
        command=CMD_RESUME_JOB,
    ),
    XtoolButtonEntityDescription(
        key="cancel_job",
        translation_key="cancel_job",
        icon="mdi:stop",
        command=CMD_CANCEL_JOB,
    ),
    XtoolButtonEntityDescription(
        key="home_all",
        translation_key="home_all",
        icon="mdi:home",
        command=CMD_HOME_ALL,
    ),
    XtoolButtonEntityDescription(
        key="home_xy",
        translation_key="home_xy",
        icon="mdi:axis-arrow",
        command=CMD_HOME_XY,
    ),
    XtoolButtonEntityDescription(
        key="home_z",
        translation_key="home_z",
        icon="mdi:axis-z-arrow",
        command=CMD_HOME_Z,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool button entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        XtoolButton(coordinator, description) for description in BUTTON_DESCRIPTIONS
    )


class XtoolButton(XtoolEntity, ButtonEntity):
    """Representation of an xTool action button."""

    entity_description: XtoolButtonEntityDescription

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        description: XtoolButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.serial_number}_{description.key}"

    async def async_press(self) -> None:
        """Handle button press."""
        await self.coordinator.send_command(self.entity_description.command)
