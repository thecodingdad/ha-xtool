"""The xTool Laser integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

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
from .coordinator import XtoolCoordinator
from .protocols import LaserInfo, detect_model

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SENSOR,
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.UPDATE,
]

type XtoolConfigEntry = ConfigEntry[XtoolCoordinator]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the xTool integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: XtoolConfigEntry) -> bool:
    """Set up xTool Laser from a config entry."""
    host = entry.data[CONF_HOST]
    device_name = entry.data.get("device_name", DEFAULT_DEVICE_NAME)
    serial_number = entry.data.get("serial_number", "")
    firmware_version = entry.data.get("firmware_version", "")
    model = detect_model(device_name)
    if model.protocol_class is None or model.coordinator_class is None:
        raise RuntimeError(
            f"Unknown xTool model {device_name!r} — cannot pick a protocol"
        )

    power_switch_entity_id = entry.options.get(CONF_POWER_SWITCH)
    enable_firmware_updates = entry.options.get(CONF_ENABLE_UPDATES, False)
    has_ap2 = entry.options.get(CONF_HAS_AP2, False)

    # Polling intervals — options stored in user-friendly units (firmware in
    # hours; everything else in seconds). Convert before handing to the
    # coordinator, which always expects seconds.
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    firmware_check_hours = entry.options.get(
        CONF_FIRMWARE_CHECK_INTERVAL, FIRMWARE_CHECK_INTERVAL // 3600
    )
    firmware_check_interval = int(firmware_check_hours) * 3600
    ap2_poll_interval = entry.options.get(
        CONF_AP2_POLL_INTERVAL, DEFAULT_AP2_POLL_INTERVAL
    )
    stats_poll_interval = entry.options.get(
        CONF_STATS_POLL_INTERVAL, DEFAULT_STATS_POLL_INTERVAL
    )
    dongle_poll_interval = entry.options.get(
        CONF_DONGLE_POLL_INTERVAL, DEFAULT_DONGLE_POLL_INTERVAL
    )

    protocol = model.protocol_class(host)

    coordinator = model.coordinator_class(
        hass,
        protocol=protocol,
        device_name=device_name,
        serial_number=serial_number,
        firmware_version=firmware_version,
        model=model,
        power_switch_entity_id=power_switch_entity_id,
        enable_firmware_updates=enable_firmware_updates,
        has_ap2=has_ap2,  # only S1Coordinator reads it; base ignores
        scan_interval=scan_interval,
        firmware_check_interval=firmware_check_interval,
        ap2_poll_interval=ap2_poll_interval,
        stats_poll_interval=stats_poll_interval,
        dongle_poll_interval=dongle_poll_interval,
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
