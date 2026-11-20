"""Config flow for the xTool integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    ColorRGBSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_DONGLE_POLL_INTERVAL,
    CONF_ENABLE_UPDATES,
    CONF_OVERLAY_COLORS,
    CONF_FIRMWARE_CHECK_INTERVAL,
    CONF_PAIRING_TOKEN,
    CONF_POWER_SWITCH,
    CONF_SCAN_INTERVAL,
    CONF_STATS_POLL_INTERVAL,
    DEFAULT_DEVICE_NAME,
    DEFAULT_DONGLE_POLL_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STATS_POLL_INTERVAL,
    DOMAIN,
    FIRMWARE_CHECK_INTERVAL,
)
from .discovery import DiscoveredDevice, discover_devices
from .protocols import (
    ConnectionInfo,
    VALIDATION_ERROR_PAIRING_REQUIRED,
    VALIDATION_ERROR_UDP_NO_REPLY,
    VALIDATION_ERROR_UNKNOWN_MODEL,
    detect_models,
    manual_model_options,
    validate_connection,
    validate_with_model,
)

# Deep-link target so users landing on the manual model picker
# without a matching entry can file a "model X / firmware Y not
# supported" issue with the discovered identifiers baked in. The
# ``new?template=…`` part jumps straight into the bug-report form
# rather than the generic issue list.
_ISSUE_TRACKER_URL = (
    "https://github.com/thecodingdad/ha-xtool/issues/new?template=bug_report.yml"
)

_LOGGER = logging.getLogger(__name__)

# How long the pairing step waits for the button press. The device
# usually ends the window itself first (auth-status 410).
PAIRING_TIMEOUT = 120.0


def _entry_data(host: str, conn_info: ConnectionInfo) -> dict[str, Any]:
    return {
        CONF_HOST: host,
        "serial_number": conn_info.serial_number,
        "device_name": conn_info.name,
        "firmware_version": conn_info.firmware_version,
        "laser_power_watts": conn_info.laser_power_watts,
        "model_key": f"{conn_info.model_id}_{conn_info.protocol_version}",
        CONF_PAIRING_TOKEN: conn_info.pairing_token,
    }


class XtoolConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for xTool."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_host: str | None = None
        self._discovered_name: str | None = None
        self._discovered_devices: list[DiscoveredDevice] = []
        # Carries the host across an `udp_no_reply` / `unknown_model`
        # fallback into the manual model-picker step.
        self._fallback_host: str | None = None
        # Cached discovery record for the fallback path so the manual
        # picker can render the discovered name + firmware verbatim
        # (helps the user file a complete bug report).
        self._fallback_discovered: DiscoveredDevice | None = None
        # Pairing step state: target host, the model to validate against
        # (``None`` = identify via UDP), the token to present, the running
        # pairing task and its outcome.
        self._pair_host: str | None = None
        self._pair_model_key: str | None = None
        self._pair_token = ""
        self._pair_task: asyncio.Task[ConnectionInfo | str] | None = None
        self._pair_result: ConnectionInfo | None = None
        self._pair_error: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return XtoolOptionsFlow(config_entry)

    async def async_step_dhcp(
        self, discovery_info: Any
    ) -> ConfigFlowResult:
        """Handle DHCP discovery of an xTool device."""
        entry = self._entry_with_mac(discovery_info.macaddress)
        if entry is not None:
            return await self._async_follow_address(entry, discovery_info.ip)
        return await self._async_handle_discovery(discovery_info.ip)

    @callback
    def _entry_with_mac(self, mac: str) -> ConfigEntry | None:
        """The laser already set up whose device has this MAC address."""
        connection = (dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))
        dev_reg = dr.async_get(self.hass)
        for entry in self._async_current_entries(include_ignore=False):
            if entry.unique_id and any(
                connection in device.connections
                for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
            ):
                return entry
        return None

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """The integration found a configured laser at an address.

        Sent by the coordinator (``async_found_at``) after the laser
        answered there with its serial number.
        """
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, discovery_info["serial_number"],
        )
        if entry is None:
            return self.async_abort(reason="already_configured")
        return await self._async_follow_address(entry, discovery_info[CONF_HOST])

    async def _async_follow_address(self, entry: ConfigEntry, host: str) -> ConfigFlowResult:
        """A configured laser was seen at ``host``.

        The address in use is kept while it answers, unless the new one is
        the laser's preferred one (its Ethernet). Otherwise the entry gets
        the new address, which the update listener uses without a reload.
        A DHCP sighting is not asked first: the MAC address says which laser
        it is, and the laser asks for its address before it answers.
        """
        await self.async_set_unique_id(entry.unique_id)
        current = entry.data.get(CONF_HOST)
        if host != current:
            coordinator = getattr(entry, "runtime_data", None)
            prefers = getattr(coordinator, "prefers_host", None)
            if not (prefers is not None and prefers(host)) and await self._configured_host_answers(
                entry.unique_id, host,
            ):
                return self.async_abort(reason="already_configured")
        # The update listener moves the running laser to the new address
        self._abort_if_unique_id_configured(updates={CONF_HOST: host}, reload_on_update=False)
        return self.async_abort(reason="already_configured")

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
        if conn_info == VALIDATION_ERROR_PAIRING_REQUIRED:
            self._pair_host = host
            self.context["title_placeholders"] = {"name": name or host}
            return await self.async_step_pair_confirm()
        if not isinstance(conn_info, ConnectionInfo):
            # Discovery flow has no form to surface ``errors`` on, so the
            # granular reason rides through the abort dialog instead.
            return self.async_abort(reason=conn_info or "cannot_connect")

        if conn_info.serial_number:
            entry = self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, conn_info.serial_number,
            )
            if entry is not None:
                return await self._async_follow_address(entry, host)
            await self.async_set_unique_id(conn_info.serial_number)

        self._discovered_host = host
        self._discovered_name = conn_info.name or name

        self.context["title_placeholders"] = {"name": self._discovered_name}
        return await self.async_step_discovery_confirm()

    async def _configured_host_answers(self, serial: str, host: str) -> bool:
        """True when this laser's entry has another address that still answers."""
        for entry in self._async_current_entries(include_ignore=False):
            current = entry.data.get(CONF_HOST)
            if entry.unique_id != serial or not current or current == host:
                continue
            info = await validate_connection(current)
            return isinstance(info, ConnectionInfo) and info.serial_number == serial
        return False

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

        # Build selection list: discovered devices + manual option.
        # Devices whose name doesn't match any registered model get a
        # suffix so the user can spot them in the dropdown — selecting
        # one routes through the existing ``unknown_model`` fallback,
        # but at least the user gets a signal up front (and can paste
        # the discovered name into a bug report).
        options = {}
        for device in self._discovered_devices:
            base = f"{device.name} ({device.ip})"
            if detect_models(device.name):
                options[device.ip] = base
            else:
                options[device.ip] = f"{base} — unsupported, please open an issue"
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
        if conn_info == VALIDATION_ERROR_PAIRING_REQUIRED:
            self._pair_host = host
            return await self.async_step_pair_confirm()
        if not isinstance(conn_info, ConnectionInfo):
            reason = conn_info or "cannot_connect"
            # Discovery couldn't identify the device — fall back to the
            # manual model picker so the user can pick the right model
            # (e.g. multicast blocked, firewall, custom firmware name).
            if reason in (
                VALIDATION_ERROR_UDP_NO_REPLY,
                VALIDATION_ERROR_UNKNOWN_MODEL,
            ):
                self._fallback_host = host
                self._fallback_discovered = next(
                    (d for d in self._discovered_devices if d.ip == host),
                    None,
                )
                if (
                    reason == VALIDATION_ERROR_UNKNOWN_MODEL
                    and self._fallback_discovered is not None
                ):
                    d = self._fallback_discovered
                    _LOGGER.info(
                        "xTool device at %s responded to discovery but its model "
                        "is not recognised — name=%r firmware=%r protocol=%r "
                        "serial=%r. Please open an issue at %s with these "
                        "values so the model can be added.",
                        host, d.name, d.version, d.protocol_version,
                        d.serial_number, _ISSUE_TRACKER_URL,
                    )
                return await self.async_step_pick_model()
            if errors is not None:
                errors["base"] = reason
                return self.async_show_form(
                    step_id="manual",
                    data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
                    errors=errors,
                )
            return self.async_abort(reason=reason)

        if conn_info.serial_number:
            await self.async_set_unique_id(conn_info.serial_number)
            self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=conn_info.name, data=_entry_data(host, conn_info),
        )

    async def async_step_pick_model(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Manual model selection — fallback when discovery cannot
        identify the device. The user picks the (model, firmware
        version) pair from a dropdown and we go straight to the
        protocol-level handshake.
        """
        errors: dict[str, str] = {}
        host = self._fallback_host or ""

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            composite_key = user_input["model"]
            model_id, _, proto_ver = composite_key.rpartition("_")
            conn_info = await validate_with_model(host, model_id, proto_ver)
            if conn_info == VALIDATION_ERROR_PAIRING_REQUIRED:
                self._pair_host = host
                self._pair_model_key = composite_key
                return await self.async_step_pair_confirm()
            if isinstance(conn_info, ConnectionInfo):
                if conn_info.serial_number:
                    await self.async_set_unique_id(conn_info.serial_number)
                    self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=conn_info.name, data=_entry_data(host, conn_info),
                )
            errors["base"] = conn_info or "cannot_connect"
            self._fallback_host = host

        discovered = self._fallback_discovered
        return self.async_show_form(
            step_id="pick_model",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=host): str,
                    vol.Required("model"): vol.In(manual_model_options()),
                }
            ),
            description_placeholders={
                "name": discovered.name if discovered else "",
                "firmware": discovered.version if discovered else "",
                "issue_url": _ISSUE_TRACKER_URL,
            },
            errors=errors,
        )


    async def async_step_reauth(
        self, entry_data: dict[str, Any],
    ) -> ConfigFlowResult:
        """The device rejected the stored pairing token — pair again."""
        self._pair_host = entry_data[CONF_HOST]
        self._pair_model_key = entry_data.get("model_key")
        self._pair_token = ""
        return await self.async_step_pair_confirm()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Change the IP address and/or pair the device again."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            self._pair_host = user_input[CONF_HOST].strip()
            self._pair_model_key = entry.data.get("model_key")
            self._pair_token = (
                "" if user_input.get("pair_again")
                else entry.data.get(CONF_PAIRING_TOKEN, "")
            )
            return await self.async_step_pair_confirm()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                vol.Optional("pair_again", default=False): bool,
            }),
        )

    async def async_step_pair_confirm(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Explain the button press, then start pairing on submit."""
        if user_input is not None:
            self._pair_error = None
            return await self.async_step_pair()
        return self._pair_confirm_form()

    async def async_step_pair_failed(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Show why pairing failed; only a new submit starts it again.

        Home Assistant passes the input that started the pairing on to the
        step after the progress. Taken as a submit, a pairing that ends
        before its first await (such as an unknown model) would start
        again at once, in a loop that blocks Home Assistant.
        """
        return self._pair_confirm_form()

    def _pair_confirm_form(self) -> ConfigFlowResult:
        errors = {"base": self._pair_error} if self._pair_error else {}
        return self.async_show_form(
            step_id="pair_confirm",
            description_placeholders={"host": self._pair_host or ""},
            errors=errors,
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Connect and wait for the button press on the device."""
        if self._pair_task is None:
            self._pair_task = self.hass.async_create_task(self._async_pair())
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="press_button",
                progress_task=self._pair_task,
                description_placeholders={"host": self._pair_host or ""},
            )
        result = self._pair_task.result()
        self._pair_task = None
        if isinstance(result, ConnectionInfo):
            self._pair_result = result
            return self.async_show_progress_done(next_step_id="pair_finish")
        self._pair_error = result or "cannot_connect"
        return self.async_show_progress_done(next_step_id="pair_failed")

    async def _async_pair(self) -> ConnectionInfo | str:
        host = self._pair_host or ""
        if self._pair_model_key:
            model_id, _, proto_ver = self._pair_model_key.rpartition("_")
            return await validate_with_model(
                host, model_id, proto_ver,
                pairing_token=self._pair_token, pairing_timeout=PAIRING_TIMEOUT,
            )
        return await validate_connection(
            host, pairing_token=self._pair_token, pairing_timeout=PAIRING_TIMEOUT,
        )

    async def async_step_pair_finish(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Store the paired device: new entry, or update the existing one."""
        conn_info = self._pair_result
        assert conn_info is not None and self._pair_host is not None
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            entry = (
                self._get_reauth_entry() if self.source == SOURCE_REAUTH
                else self._get_reconfigure_entry()
            )
            if conn_info.serial_number:
                await self.async_set_unique_id(conn_info.serial_number)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    CONF_HOST: self._pair_host,
                    CONF_PAIRING_TOKEN: conn_info.pairing_token,
                },
            )
        if conn_info.serial_number:
            await self.async_set_unique_id(conn_info.serial_number)
            self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=conn_info.name, data=_entry_data(self._pair_host, conn_info),
        )


class XtoolOptionsFlow(OptionsFlow):
    """Handle xTool options."""

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

        # S1-only: per-poll cadences
        if is_s1:
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

        # P2: the camera overlays' colours, in a section that opens
        if "overview" in (getattr(model, "camera_names", ()) or ()):
            from .protocols.rest.p2_geometry import OVERLAY_COLORS

            chosen = current_options.get(CONF_OVERLAY_COLORS) or {}
            schema_dict[vol.Required(CONF_OVERLAY_COLORS)] = section(
                vol.Schema({
                    vol.Optional(key, default=list(chosen.get(key, default))): ColorRGBSelector()
                    for key, default in OVERLAY_COLORS.items()
                }),
                {"collapsed": True},
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
