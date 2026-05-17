"""The xTool Laser integration."""

from __future__ import annotations

import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DONGLE_POLL_INTERVAL,
    CONF_ENABLE_UPDATES,
    CONF_FIRMWARE_CHECK_INTERVAL,
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
from .coordinator import XtoolCoordinator
from .protocols import DEVICE_MODELS, LaserInfo, detect_model

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.EVENT,
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

    # ``model_key`` (single composite field, e.g. ``"F2UltraUV_V2"``)
    # is the source of truth for which ``XtoolDeviceModel`` entry
    # this config refers to. Legacy entries persisted ``model_id`` +
    # ``protocol_version`` as separate fields; compose them once and
    # migrate forward.
    persisted_model_key = entry.data.get("model_key")
    legacy_model_id = entry.data.get("model_id")
    legacy_protocol_version = entry.data.get("protocol_version")
    if persisted_model_key is None and legacy_model_id:
        # One-shot migration: combine the two old fields into the
        # canonical composite key.
        persisted_model_key = (
            f"{legacy_model_id}_{legacy_protocol_version or 'V1'}"
        )

    model = (
        DEVICE_MODELS.get(persisted_model_key)
        if persisted_model_key else None
    )
    if model is None:
        model = detect_model(device_name)
    if model.protocol_class is None or model.coordinator_class is None:
        raise RuntimeError(
            f"Unknown xTool model {device_name!r} — cannot pick a protocol"
        )

    # Auto-upgrade legacy V1 entries that were created before the
    # v2.2.0 V2 split. If a ``_V2`` sibling exists in DEVICE_MODELS
    # and the device answers a port-28900 TLS probe, switch in-place.
    # Without this, a V1 entry on V2-firmware hardware keeps hitting
    # the port-8080 REST endpoints firmware no longer serves and
    # entities never populate.
    if model.protocol_version == "V1":
        v2_model = DEVICE_MODELS.get(f"{model.model_id}_V2")
        if v2_model is not None and v2_model.protocol_class is not None:
            try:
                from .protocols import probe_v2
                v2_alive = await probe_v2(host)
            except Exception as err:
                _LOGGER.debug("V2 probe failed for %s: %s", host, err)
                v2_alive = False
            if v2_alive:
                _LOGGER.info(
                    "xTool %s at %s: legacy V1 config entry on a V2-firmware "
                    "device — upgrading to V2 protocol",
                    model.model_id, host,
                )
                model = v2_model

    # Normalise entry.data: persist ``model_key`` as the single
    # source of truth and drop the legacy ``model_id`` +
    # ``protocol_version`` fields.
    canonical_key = f"{model.model_id}_{model.protocol_version}"
    new_data = dict(entry.data)
    data_dirty = False
    if new_data.get("model_key") != canonical_key:
        new_data["model_key"] = canonical_key
        data_dirty = True
    for stale in ("model_id", "protocol_version"):
        if stale in new_data:
            new_data.pop(stale)
            data_dirty = True
    if data_dirty:
        hass.config_entries.async_update_entry(entry, data=new_data)

    power_switch_entity_id = entry.options.get(CONF_POWER_SWITCH)
    enable_firmware_updates = entry.options.get(CONF_ENABLE_UPDATES, False)

    # Polling intervals — options stored in user-friendly units (firmware in
    # hours; everything else in seconds). Convert before handing to the
    # coordinator, which always expects seconds.
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    firmware_check_hours = entry.options.get(
        CONF_FIRMWARE_CHECK_INTERVAL, FIRMWARE_CHECK_INTERVAL // 3600
    )
    firmware_check_interval = int(firmware_check_hours) * 3600
    stats_poll_interval = entry.options.get(
        CONF_STATS_POLL_INTERVAL, DEFAULT_STATS_POLL_INTERVAL
    )
    dongle_poll_interval = entry.options.get(
        CONF_DONGLE_POLL_INTERVAL, DEFAULT_DONGLE_POLL_INTERVAL
    )

    # Each model entry already names its concrete protocol_class, so no
    # V1/V2 switching is needed at runtime — the right entry was picked
    # at config-flow validation time.
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
        scan_interval=scan_interval,
        firmware_check_interval=firmware_check_interval,
        stats_poll_interval=stats_poll_interval,
        dongle_poll_interval=dongle_poll_interval,
    )
    laser_power_watts = entry.data.get("laser_power_watts", 0)
    if laser_power_watts:
        coordinator.laser = LaserInfo(power_watts=laser_power_watts)

    await coordinator.async_config_entry_first_refresh()

    # Persist the serial number back to the config entry if discovery
    # filled it in — earlier broken probes / setup runs could have
    # stashed an empty value.
    if coordinator.serial_number and entry.data.get("serial_number") != coordinator.serial_number:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "serial_number": coordinator.serial_number},
            unique_id=coordinator.serial_number,
        )

    # Entity-id migration was previously run on every setup. It
    # ping-ponged friendly ↔ serial-prefixed names on each reload
    # (Issue #4, v2.5.4 retest), and it crashed on accessory
    # unique_ids that contain colons. HA's native suggested_object_id
    # already produces the right shape on fresh registrations; the
    # old re-stamp is no longer needed for new installs. Leaving the
    # function around for now in case we need a clean migration pass
    # in a future release, but it is no longer wired in.
    # _migrate_entity_registry(hass, entry, coordinator)

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


_LOGGER = logging.getLogger(__name__)

_OBJECT_ID_MODEL_SLUG_RE = re.compile(r"[^a-z0-9]")


def _migrate_entity_registry(
    hass: HomeAssistant, entry: XtoolConfigEntry, coordinator: XtoolCoordinator,
) -> None:
    """Re-stamp ``entity_id`` with the SN-prefix shape + drop stale entries.

    The integration's ``XtoolEntity._set_unique_id`` declares
    ``suggested_object_id`` = ``xtool_<model_slug>_<serial>_<key>``
    so fresh entity-ids carry the serial number. HA only honors
    ``suggested_object_id`` on **first** registration; entries that
    were created during an earlier setup run with an empty serial
    keep their unrelated entity-ids forever unless something
    re-stamps them.

    Walks every entity registered against this config entry once
    per setup:

    * **Drop** entries whose ``unique_id`` doesn't match the current
      coordinator's ``{serial}_…`` prefix (or the accessory
      ``{serial}_accessory_…`` shape). These are leftovers from
      a broken-SN era and can't be reattached to the live
      coordinator state.
    * **Rename** entries that match — compute the new object-id and,
      if it differs from the stored one, call
      ``async_update_entity`` so the registry picks up the new
      entity-id. HA tracks state under the renamed id automatically.
    """
    sid = coordinator.serial_number
    if not sid:
        return
    model_slug = _OBJECT_ID_MODEL_SLUG_RE.sub(
        "", coordinator.model.model_id.lower()
    )
    registry = er.async_get(hass)
    sid_prefix = f"{sid}_"
    # Walk a snapshot — we mutate the registry inside the loop.
    entries = list(registry.entities.values())
    for ent in entries:
        if ent.config_entry_id != entry.entry_id:
            continue
        unique_id = ent.unique_id or ""
        if not unique_id.startswith(sid_prefix):
            # Orphan from an earlier setup run with a different /
            # empty serial number. Nothing we can do with these —
            # state will never refresh because no live entity owns
            # the unique_id. Drop them so the user sees a clean
            # device page.
            _LOGGER.debug(
                "xtool registry cleanup: removing stale entity %s "
                "(unique_id=%r doesn't match current serial %s)",
                ent.entity_id, unique_id, sid,
            )
            registry.async_remove(ent.entity_id)
            continue
        # The key portion after the serial prefix is what
        # ``_set_unique_id`` originally stamped.
        key = unique_id[len(sid_prefix):]
        platform = ent.entity_id.split(".", 1)[0]
        desired = f"{platform}.xtool_{model_slug}_{sid.lower()}_{key}"
        if ent.entity_id == desired:
            continue
        # Idempotency guard: if the desired ID is already registered
        # under a *different* unique_id, a separate physical entity
        # owns the slot — leave both alone. If the same unique_id
        # owns it (legacy + new co-exist from a prior cycle where HA
        # auto-generated the new form alongside the legacy one), drop
        # the legacy sibling so the recorder can rebind history to
        # the canonical slot without the "new entity_id already in
        # use" warning.
        existing = registry.async_get(desired)
        if existing is not None:
            if existing.unique_id != ent.unique_id:
                _LOGGER.debug(
                    "xtool registry migrate: %s → %s skipped — target "
                    "already owned by unique_id %r",
                    ent.entity_id, desired, existing.unique_id,
                )
                continue
            _LOGGER.debug(
                "xtool registry cleanup: removed orphaned legacy "
                "entry %s (unique_id=%r already owns canonical %s)",
                ent.entity_id, ent.unique_id, desired,
            )
            registry.async_remove(ent.entity_id)
            continue
        try:
            registry.async_update_entity(ent.entity_id, new_entity_id=desired)
            _LOGGER.debug(
                "xtool registry migrate: %s → %s",
                ent.entity_id, desired,
            )
        except (KeyError, ValueError) as err:
            _LOGGER.debug(
                "xtool registry migrate: %s → %s failed: %s",
                ent.entity_id, desired, err,
            )


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


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow the user to remove an accessory device that's no longer connected.

    HA shows the "Delete" button per-integration (not per-device), so all
    xTool devices surface the option in the UI. The hook gates the actual
    removal:

    - Stale device (no entities left) — always removable. Catches orphans
      left behind by earlier registry rotations (empty-SN entries from
      broken probes, accessories the user unpaired, …).
    - Active laser device: never removable — raised as a clear error so
      the user sees *why* the click failed.
    - Connected accessory child: blocked with an error mentioning the
      accessory's friendly name so the user knows to unpair first.
    - Disconnected accessory child: allowed; returning True lets HA drop
      the device + its entities from the registry.
    """
    # Devices with zero entities are always orphans we can drop.
    registry = er.async_get(hass)
    has_entities = any(
        ent.device_id == device_entry.id
        for ent in registry.entities.values()
    )
    if not has_entities:
        return True

    coordinator = entry.runtime_data
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        # Laser device identifier = the bare serial number (no ':' separator).
        # Accessory child = "<laser_sn>:<type>:<acc_sn>".
        parts = identifier.split(":", 2)
        if len(parts) < 3:
            raise HomeAssistantError(
                "The laser device itself can't be deleted from the device "
                "page. Remove the xTool integration via Settings → Devices & "
                "Services → xTool Laser → Delete instead."
            )
        _laser_sn, type_id, acc_sn = parts
        key = f"{type_id}:{acc_sn}"
        if (
            coordinator.data
            and coordinator.data.connected_accessories
            and key in coordinator.data.connected_accessories
        ):
            name = device_entry.name_by_user or device_entry.name or type_id
            raise HomeAssistantError(
                f"{name} is currently connected to the laser. Unpair the "
                f"accessory first (or unplug a wired one), then delete it "
                f"here."
            )
        return True
    return False
