"""The xTool Laser integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_POWER_SWITCH, DEFAULT_DEVICE_NAME, DOMAIN
from .coordinator import XtoolCoordinator
from .http_mcode_protocol import HttpMcodeProtocol
from .models import detect_model
from .protocol import LaserInfo, ProtocolType, XtoolProtocol
from .rest_protocol import RestProtocol
from .ws_protocol import WsMcodeProtocol

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SENSOR,
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
]

type XtoolConfigEntry = ConfigEntry[XtoolCoordinator]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the xTool integration."""
    return True


def _create_protocol(host: str, protocol_type: str) -> XtoolProtocol:
    """Create the correct protocol instance based on the detected type."""
    if protocol_type == ProtocolType.WS_MCODE:
        return WsMcodeProtocol(host)
    if protocol_type == ProtocolType.HTTP_MCODE:
        return HttpMcodeProtocol(host)
    return RestProtocol(host)


async def async_setup_entry(hass: HomeAssistant, entry: XtoolConfigEntry) -> bool:
    """Set up xTool Laser from a config entry."""
    host = entry.data[CONF_HOST]
    device_name = entry.data.get("device_name", DEFAULT_DEVICE_NAME)
    serial_number = entry.data.get("serial_number", "")
    firmware_version = entry.data.get("firmware_version", "")
    protocol_type = entry.data.get("protocol_type", ProtocolType.WS_MCODE)
    model = detect_model(device_name)

    power_switch_entity_id = entry.options.get(CONF_POWER_SWITCH)

    protocol = _create_protocol(host, protocol_type)

    coordinator = XtoolCoordinator(
        hass,
        protocol=protocol,
        device_name=device_name,
        serial_number=serial_number,
        firmware_version=firmware_version,
        model=model,
        power_switch_entity_id=power_switch_entity_id,
    )
    laser_power_watts = entry.data.get("laser_power_watts", 0)
    if laser_power_watts:
        coordinator.laser = LaserInfo(power_watts=laser_power_watts)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: XtoolConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
    return unload_ok


async def _async_options_updated(
    hass: HomeAssistant, entry: XtoolConfigEntry
) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
