"""Actions of the xTool integration."""

from __future__ import annotations

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
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

FOCUS_SCHEMA = {
    vol.Exclusive(ATTR_STEP, "move"): vol.Coerce(float),
    vol.Optional(ATTR_STEPS, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=1500)),
    vol.Exclusive(ATTR_THICKNESS, "move"): vol.All(vol.Coerce(float), vol.Range(min=0, max=150)),
}

_CROP_SIDE = vol.All(vol.Coerce(float), vol.Range(min=0, max=460))
_CROP_CENTER = vol.All(vol.Coerce(float), vol.Range(min=-200, max=200))
CAMERA_SCHEMA = {
    vol.Optional(ATTR_CROP_WIDTH): _CROP_SIDE,
    vol.Optional(ATTR_CROP_HEIGHT): _CROP_SIDE,
    vol.Optional(ATTR_CROP_CENTER_X): _CROP_CENTER,
    vol.Optional(ATTR_CROP_CENTER_Y): _CROP_CENTER,
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
    async_register_platform_entity_service(
        hass,
        DOMAIN,
        "camera",
        entity_domain=CAMERA_DOMAIN,
        schema=CAMERA_SCHEMA,
        func=_async_camera,
    )
