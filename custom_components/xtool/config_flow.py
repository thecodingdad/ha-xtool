"""Config flow for xTool Laser integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import CONF_POWER_SWITCH, DEFAULT_DEVICE_NAME, DOMAIN
from .discovery import DiscoveredDevice, discover_devices
from .protocol import validate_connection

_LOGGER = logging.getLogger(__name__)


class XtoolConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for xTool Laser."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_host: str | None = None
        self._discovered_name: str | None = None
        self._discovered_devices: list[DiscoveredDevice] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return XtoolOptionsFlow(config_entry)

    async def async_step_dhcp(
        self, discovery_info: Any
    ) -> ConfigFlowResult:
        """Handle DHCP discovery of an xTool device."""
        return await self._async_handle_discovery(discovery_info.ip)

    async def async_step_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle UDP discovery of an xTool device."""
        return await self._async_handle_discovery(
            discovery_info["host"], discovery_info.get("name")
        )

    async def _async_handle_discovery(
        self, host: str, name: str | None = None
    ) -> ConfigFlowResult:
        """Common handler for DHCP and UDP discovery."""
        _LOGGER.debug("Discovery: %s (%s)", name or "unknown", host)

        conn_info = await validate_connection(host)
        if conn_info is None:
            return self.async_abort(reason="cannot_connect")

        if conn_info.serial_number:
            await self.async_set_unique_id(conn_info.serial_number)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._discovered_host = host
        self._discovered_name = conn_info.name or name

        self.context["title_placeholders"] = {"name": self._discovered_name}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovered device."""
        if user_input is not None:
            return await self._async_create_from_host(self._discovered_host)

        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": self._discovered_name or DEFAULT_DEVICE_NAME},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user-initiated setup — scan network first, then manual fallback."""
        if user_input is not None:
            if user_input.get("device") == "_manual_":
                return await self.async_step_manual()
            # User selected a discovered device
            host = user_input["device"]
            return await self._async_create_from_host(host)

        # Run UDP discovery scan
        self._discovered_devices = await discover_devices(timeout=3.0)

        if not self._discovered_devices:
            # No devices found — go directly to manual entry
            return await self.async_step_manual()

        # Build selection list: discovered devices + manual option
        options = {
            device.ip: f"{device.name} ({device.ip})"
            for device in self._discovered_devices
        }
        options["_manual_"] = "Enter IP address manually..."

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(options),
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual IP entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            return await self._async_create_from_host(host, errors)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def _async_create_from_host(
        self, host: str | None, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Validate and create config entry from a host IP."""
        if host is None:
            if errors is not None:
                errors["base"] = "cannot_connect"
            return await self.async_step_manual()

        conn_info = await validate_connection(host)
        if conn_info is None:
            if errors is not None:
                errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id="manual",
                    data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
                    errors=errors,
                )
            return self.async_abort(reason="cannot_connect")

        if conn_info.serial_number:
            await self.async_set_unique_id(conn_info.serial_number)
            self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=conn_info.name,
            data={
                CONF_HOST: host,
                "serial_number": conn_info.serial_number,
                "device_name": conn_info.name,
                "firmware_version": conn_info.firmware_version,
                "laser_power_watts": conn_info.laser_power_watts,
                "protocol_type": conn_info.protocol_type,
            },
        )


class XtoolOptionsFlow(OptionsFlow):
    """Handle xTool Laser options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_switch = self._config_entry.options.get(CONF_POWER_SWITCH)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POWER_SWITCH,
                        description={"suggested_value": current_switch},
                    ): EntitySelector(
                        EntitySelectorConfig(filter={"domain": "switch"})
                    ),
                }
            ),
        )
