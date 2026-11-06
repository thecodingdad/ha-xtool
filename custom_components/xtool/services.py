"""Actions of the xTool integration."""

from __future__ import annotations

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_platform_entity_service
import voluptuous as vol

from .const import DOMAIN

ATTR_STEP = "step"
ATTR_STEPS = "steps"
ATTR_THICKNESS = "thickness"

FOCUS_SCHEMA = {
    vol.Exclusive(ATTR_STEP, "move"): vol.Coerce(float),
    vol.Optional(ATTR_STEPS, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=1500)),
    vol.Exclusive(ATTR_THICKNESS, "move"): vol.All(vol.Coerce(float), vol.Range(min=0, max=150)),
}


async def _async_focus(entity, call: ServiceCall) -> None:
    """Move the focus by a signed step (times ``steps``) or to a thickness."""
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
    await focus(
        step=data.get(ATTR_STEP),
        steps=data[ATTR_STEPS],
        thickness=data.get(ATTR_THICKNESS),
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
    )
