"""Actions of the xTool integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import async_register_platform_entity_service
import voluptuous as vol

from .const import DOMAIN

ATTR_STEP = "step"
ATTR_STEPS = "steps"
ATTR_THICKNESS = "thickness"
ATTR_CROP_WIDTH = "crop_width"
ATTR_CROP_HEIGHT = "crop_height"
ATTR_CROP_CENTER_X = "crop_center_x"
ATTR_CROP_CENTER_Y = "crop_center_y"
ATTR_X = "x"
ATTR_Y = "y"
ATTR_STEP_X = "step_x"
ATTR_STEP_Y = "step_y"
ATTR_DEVICE_ID = "device_id"

FOCUS_SCHEMA = {
    vol.Exclusive(ATTR_STEP, "move"): vol.Coerce(float),
    vol.Optional(ATTR_STEPS, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=1500)),
    vol.Exclusive(ATTR_THICKNESS, "move"): vol.All(vol.Coerce(float), vol.Range(min=0, max=150)),
}

_MM = vol.Coerce(float)
MOVE_SCHEMA = {
    vol.Required(ATTR_DEVICE_ID): cv.string,
    vol.Exclusive(ATTR_X, "x"): _MM,
    vol.Exclusive(ATTR_STEP_X, "x"): _MM,
    vol.Exclusive(ATTR_Y, "y"): _MM,
    vol.Exclusive(ATTR_STEP_Y, "y"): _MM,
    vol.Optional(ATTR_STEPS, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
}

# The limits depend on the camera (its own crop numbers), so the schema only
# converts the values; _async_camera checks them against that camera.
_CROP_VALUE = vol.Coerce(float)
CAMERA_SCHEMA = {
    vol.Optional(ATTR_CROP_WIDTH): _CROP_VALUE,
    vol.Optional(ATTR_CROP_HEIGHT): _CROP_VALUE,
    vol.Optional(ATTR_CROP_CENTER_X): _CROP_VALUE,
    vol.Optional(ATTR_CROP_CENTER_Y): _CROP_VALUE,
}
_CROP_SETTINGS = {
    ATTR_CROP_WIDTH: "width",
    ATTR_CROP_HEIGHT: "height",
    ATTR_CROP_CENTER_X: "center_x",
    ATTR_CROP_CENTER_Y: "center_y",
}


async def _async_focus(entity, call: ServiceCall) -> ServiceResponse:
    """Move the focus by a signed step (times ``steps``) or to a thickness.

    The response gives the height moved to. A move that stopped at 0 or
    150 mm is reported as an error that says so (the move was made).
    """
    focus = getattr(entity, "async_focus", None)
    if focus is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="focus_wrong_entity",
        )
    data = call.data
    if ATTR_STEP not in data and ATTR_THICKNESS not in data:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="focus_needs_step_or_thickness",
        )
    return await focus(
        step=data.get(ATTR_STEP),
        steps=data[ATTR_STEPS],
        thickness=data.get(ATTR_THICKNESS),
    )



def _laser_of_device(hass: HomeAssistant, device_id: str) -> Any:
    """The coordinator of the xTool laser a device belongs to (None if none)."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
            return getattr(entry, "runtime_data", None)
    return None


async def _async_move(call: ServiceCall) -> ServiceResponse:
    """Move a P2's laser head to X and Y, or by signed steps (times ``steps``).

    The laser is picked by its device. The response gives the position
    moved to. A move that stopped at a limit is reported as an error that
    says so (the move was made).
    """
    from .protocols.rest.p2_entities import async_move_laser_head, p2_can_move

    coordinator = _laser_of_device(call.hass, call.data[ATTR_DEVICE_ID])
    if coordinator is None or not p2_can_move(coordinator):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="move_wrong_device",
        )
    data = call.data
    keys = (ATTR_X, ATTR_Y, ATTR_STEP_X, ATTR_STEP_Y)
    if not any(key in data for key in keys):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="move_needs_a_value",
        )
    return await async_move_laser_head(
        coordinator,
        x=data.get(ATTR_X),
        y=data.get(ATTR_Y),
        step_x=data.get(ATTR_STEP_X),
        step_y=data.get(ATTR_STEP_Y),
        steps=data[ATTR_STEPS],
    )


async def _async_camera(entity, call: ServiceCall) -> None:
    """Set the main camera's crop (any of the four values) and refresh it."""
    if not getattr(entity, "supports_crop", False):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="camera_wrong_entity",
        )
    data = call.data
    keys = (ATTR_CROP_WIDTH, ATTR_CROP_HEIGHT, ATTR_CROP_CENTER_X, ATTR_CROP_CENTER_Y)
    if not any(key in data for key in keys):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="camera_needs_a_value",
        )
    limits = entity.crop_limits
    for key, setting in _CROP_SETTINGS.items():
        if key not in data:
            continue
        minimum, maximum = limits[setting]
        if not minimum <= data[key] <= maximum:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="camera_value_out_of_range",
                translation_placeholders={
                    "field": key,
                    "value": f"{data[key]:g}",
                    "minimum": f"{minimum:g}",
                    "maximum": f"{maximum:g}",
                },
            )
    await entity.async_set_crop(
        width=data.get(ATTR_CROP_WIDTH),
        height=data.get(ATTR_CROP_HEIGHT),
        center_x=data.get(ATTR_CROP_CENTER_X),
        center_y=data.get(ATTR_CROP_CENTER_Y),
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    async_register_platform_entity_service(
        hass,
        DOMAIN,
        "focus",
        entity_domain=NUMBER_DOMAIN,
        schema=FOCUS_SCHEMA,
        func=_async_focus,
        supports_response=SupportsResponse.OPTIONAL,
    )
    # The head belongs to the laser, not to one entity: Move takes a device
    hass.services.async_register(
        DOMAIN,
        "move",
        _async_move,
        schema=vol.Schema(MOVE_SCHEMA),
        supports_response=SupportsResponse.OPTIONAL,
    )
    async_register_platform_entity_service(
        hass,
        DOMAIN,
        "camera",
        entity_domain=CAMERA_DOMAIN,
        schema=CAMERA_SCHEMA,
        func=_async_camera,
    )
