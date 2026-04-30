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
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_AP2_POLL_INTERVAL,
    CONF_DONGLE_POLL_INTERVAL,
    CONF_ENABLE_UPDATES,
    CONF_FIRMWARE_CHECK_INTERVAL,
    CONF_HAS_AP2,
    CONF_POWER_SWITCH,
    CONF_SCAN_INTERVAL,
    CONF_STATS_POLL_INTERVAL,
    DEFAULT_AP2_POLL_INTERVAL,
    DEFAULT_DEVICE_NAME,
    DEFAULT_DONGLE_POLL_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STATS_POLL_INTERVAL,
    DOMAIN,
    FIRMWARE_CHECK_INTERVAL,
)
from .discovery import DiscoveredDevice, discover_devices
from .protocols import validate_connection

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
            },
        )


class XtoolOptionsFlow(OptionsFlow):
    """Handle xTool Laser options."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry
        self._pending_options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the options form. Schema is model-aware — only options
        relevant to the connected device's protocol family are shown."""
        current_options = self._config_entry.options
        currently_enabled = current_options.get(CONF_ENABLE_UPDATES, False)

        if user_input is not None:
            # If user is turning updates ON (False -> True), require confirmation
            if user_input.get(CONF_ENABLE_UPDATES) and not currently_enabled:
                self._pending_options = user_input
                return await self.async_step_confirm_updates()
            return self.async_create_entry(data=user_input)

        current_switch = current_options.get(CONF_POWER_SWITCH)
        currently_has_ap2 = current_options.get(CONF_HAS_AP2, False)
        device_name = self._config_entry.data.get("device_name", "")

        from .protocols import detect_model
        from .protocols.s1 import S1Protocol

        model = detect_model(device_name)
        is_s1 = model.protocol_class is S1Protocol
        has_firmware = bool(model.firmware_content_id)

        # Power switch — universal
        schema_dict: dict[Any, Any] = {
            vol.Optional(
                CONF_POWER_SWITCH,
                description={"suggested_value": current_switch},
            ): EntitySelector(
                EntitySelectorConfig(filter={"domain": "switch"})
            ),
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=current_options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=60, step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }

        # Firmware update toggle + cadence — only models with a known
        # firmware_content_id can hit the cloud API meaningfully.
        if has_firmware:
            schema_dict[vol.Optional(
                CONF_ENABLE_UPDATES,
                default=currently_enabled,
            )] = bool
            schema_dict[vol.Optional(
                CONF_FIRMWARE_CHECK_INTERVAL,
                default=current_options.get(
                    CONF_FIRMWARE_CHECK_INTERVAL,
                    FIRMWARE_CHECK_INTERVAL // 3600,
                ),
            )] = NumberSelector(
                NumberSelectorConfig(
                    min=1, max=24, step=1,
                    unit_of_measurement="h",
                    mode=NumberSelectorMode.BOX,
                )
            )

        # S1-only: AP2 toggle + per-poll cadences
        if is_s1:
            schema_dict[vol.Optional(
                CONF_HAS_AP2,
                default=currently_has_ap2,
            )] = bool
            schema_dict[vol.Optional(
                CONF_AP2_POLL_INTERVAL,
                default=current_options.get(
                    CONF_AP2_POLL_INTERVAL, DEFAULT_AP2_POLL_INTERVAL
                ),
            )] = NumberSelector(
                NumberSelectorConfig(
                    min=5, max=300, step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            )
            schema_dict[vol.Optional(
                CONF_STATS_POLL_INTERVAL,
                default=current_options.get(
                    CONF_STATS_POLL_INTERVAL, DEFAULT_STATS_POLL_INTERVAL
                ),
            )] = NumberSelector(
                NumberSelectorConfig(
                    min=60, max=3600, step=10,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            )
            schema_dict[vol.Optional(
                CONF_DONGLE_POLL_INTERVAL,
                default=current_options.get(
                    CONF_DONGLE_POLL_INTERVAL, DEFAULT_DONGLE_POLL_INTERVAL
                ),
            )] = NumberSelector(
                NumberSelectorConfig(
                    min=10, max=600, step=5,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_confirm_updates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a menu warning about firmware update risks."""
        return self.async_show_menu(
            step_id="confirm_updates",
            menu_options=["enable_updates", "cancel_updates"],
        )

    async def async_step_enable_updates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """User confirmed — persist pending options with updates enabled."""
        return self.async_create_entry(data=self._pending_options)

    async def async_step_cancel_updates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """User cancelled — persist pending options with updates disabled."""
        self._pending_options[CONF_ENABLE_UPDATES] = False
        return self.async_create_entry(data=self._pending_options)
