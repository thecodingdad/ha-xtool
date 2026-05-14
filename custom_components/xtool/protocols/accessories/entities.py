"""HA entities for BT-paired accessories.

One generic entity class per HA platform, parameterised by the
:class:`AccessoryEntitySpec` declarations in :mod:`definitions`.
The accessory layer generates entities lazily (only for currently-
connected accessories) and the coordinator wires them in via
:meth:`XtoolCoordinator._dispatch_new_accessories` whenever a new
accessory pair lands.

Every accessory entity:

- Sits under a **child device** in the HA device registry
  (``identifiers={(DOMAIN, "<laser_serial>:<type>:<acc_serial>")}``,
  ``via_device=(DOMAIN, "<laser_serial>")``) so the laser device
  groups its accessories visually.
- Reports ``available=False`` whenever the accessory is no longer
  in ``state.connected_accessories`` — entities aren't deleted so
  reconnects re-populate without registry churn.
- Carries the ``_xtool_platform`` attribute the coordinator's
  dispatcher uses for per-platform routing.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory

from ...const import DOMAIN
from ...coordinator import XtoolCoordinator
from ...entity import XtoolEntity, XtoolReadOnlyEntity
from ...firmware import (
    FirmwareUpdateInfo,
    check_accessory_firmware_update,
    download_firmware,
)
from ..base import AccessoryState
from . import get_definition
from .base import (
    LASER_HOST_MCODES,
    AccessoryDefinition,
    AccessoryEntitySpec,
)

_LOGGER = logging.getLogger(__name__)


def _entity_category(value: str | None) -> EntityCategory | None:
    if value == "diagnostic":
        return EntityCategory.DIAGNOSTIC
    if value == "config":
        return EntityCategory.CONFIG
    return None


class _AccessoryEntity(XtoolEntity):
    """Common base for every accessory entity.

    Each accessory becomes its own HA child device hanging off
    the laser (``via_device``). Entity-id naming reuses the
    laser's ``xtool_{model_slug}_{serial}_*`` prefix so accessory
    entities sort alongside the laser's own in HA's UI. The
    ``has_entity_name`` flag is intentionally **off** here — that
    way HA uses ``suggested_object_id`` directly instead of
    composing the entity-id from the (much shorter) child-device
    name.
    """

    # ``has_entity_name = True`` so HA resolves the localised
    # display name via ``_attr_translation_key`` — the laser-side
    # ``XtoolEntity`` does the same, and the serial-prefixed
    # entity-id is preserved via ``suggested_object_id`` plus the
    # one-shot registry migration in ``xtool/__init__.py``.
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        definition: AccessoryDefinition,
        sn: str,
        spec: AccessoryEntitySpec,
    ) -> None:
        super().__init__(coordinator)
        self._definition = definition
        self._sn = sn
        self._spec = spec
        self._accessory_key = f"{definition.type_id}:{sn}"
        translation_key = spec.translation_key or (
            f"accessory_{definition.type_id.lower()}_{spec.key}"
        )
        self._attr_translation_key = translation_key
        unique_suffix = (
            f"accessory_{definition.type_id.lower()}_{sn}_{spec.key}"
        )
        self._set_unique_id(unique_suffix)
        if spec.icon is not None:
            self._attr_icon = spec.icon
        self._attr_entity_category = _entity_category(spec.entity_category)

    @property
    def device_info(self) -> DeviceInfo:
        laser_sid = self.coordinator.serial_number
        # Slot-based SN ("slot0", "slot2") is a synthetic
        # discriminator the M1098 path uses to keep the
        # accessory's unique-id stable across reconnects. It's
        # not a real serial — hide it from the human-facing
        # device label.
        is_synthetic_sn = (
            not self._sn or self._sn.startswith("slot")
        )
        if is_synthetic_sn:
            device_name = self._definition.friendly_name
            serial_for_info: str | None = None
        else:
            device_name = (
                f"{self._definition.friendly_name} ({self._sn})"
            )
            serial_for_info = self._sn
        return DeviceInfo(
            identifiers={(DOMAIN, f"{laser_sid}:{self._accessory_key}")},
            via_device=(DOMAIN, laser_sid),
            name=device_name,
            manufacturer="xTool",
            model=self._definition.friendly_name,
            serial_number=serial_for_info,
        )

    @property
    def _state(self) -> AccessoryState | None:
        data = self.coordinator.data
        if data is None:
            return None
        return data.connected_accessories.get(self._accessory_key)

    @property
    def available(self) -> bool:
        return super().available and self._state is not None

    def _field(self) -> Any:
        s = self._state
        if s is None or self._spec.field is None:
            return None
        return s.fields.get(self._spec.field)


class _AccessoryReadOnlyEntity(_AccessoryEntity):
    """Base for accessory sensor + binary-sensor entities.

    Stays available across laser-side outages so the last-known
    accessory field values keep rendering on dashboards. The
    accessory must still be paired (``self._state is not None``)
    — unpaired accessories really are gone and the entity goes
    unavailable.
    """

    @property
    def available(self) -> bool:
        return (
            self.coordinator.data is not None
            and self._state is not None
        )


class _AccessorySensor(_AccessoryReadOnlyEntity, SensorEntity):
    _xtool_platform = "sensor"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self._spec.unit:
            self._attr_native_unit_of_measurement = self._spec.unit
        if self._spec.device_class:
            self._attr_device_class = self._spec.device_class

    @property
    def native_value(self) -> Any:
        return self._field()


class _AccessoryBinarySensor(_AccessoryReadOnlyEntity, BinarySensorEntity):
    _xtool_platform = "binary_sensor"

    @property
    def is_on(self) -> bool | None:
        v = self._field()
        if v is None:
            return None
        return bool(v)


async def _dispatch_write(entity: _AccessoryEntity, value: Any) -> None:
    """Send the entity's interaction through the right transport.

    ``write_action`` (if set) wins — invoked with
    ``(coordinator, value)`` and expected to do the right thing
    for the laser family (V2 peripheral API, REST cmd, etc.).
    Falls back to ``write_mcode`` routing via M-code transport
    when the spec carries one but no explicit action.
    """
    spec = entity._spec
    if spec.write_action is not None:
        try:
            await spec.write_action(entity.coordinator, value)
        except Exception as err:
            _LOGGER.debug(
                "Accessory %s write_action failed: %s",
                entity._accessory_key, err,
            )
        return
    write = spec.write_mcode
    if write is None:
        return
    mcode = write(value) if callable(write) else write
    await _passthrough_write(entity, mcode)


class _AccessorySwitch(_AccessoryEntity, SwitchEntity):
    _xtool_platform = "switch"

    @property
    def is_on(self) -> bool | None:
        v = self._field()
        if v is None:
            return None
        return bool(v)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await _dispatch_write(self, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await _dispatch_write(self, False)


class _AccessorySelect(_AccessoryEntity, SelectEntity):
    _xtool_platform = "select"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._attr_options = list(self._spec.options)

    @property
    def current_option(self) -> str | None:
        v = self._field()
        return None if v is None else str(v)

    async def async_select_option(self, option: str) -> None:
        await _dispatch_write(self, option)


class _AccessoryNumber(_AccessoryEntity, NumberEntity):
    _xtool_platform = "number"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self._spec.min_value is not None:
            self._attr_native_min_value = self._spec.min_value
        if self._spec.max_value is not None:
            self._attr_native_max_value = self._spec.max_value
        if self._spec.step is not None:
            self._attr_native_step = self._spec.step
        if self._spec.unit:
            self._attr_native_unit_of_measurement = self._spec.unit

    @property
    def native_value(self) -> float | None:
        v = self._field()
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await _dispatch_write(self, value)


class _AccessoryButton(_AccessoryEntity, ButtonEntity):
    _xtool_platform = "button"

    async def async_press(self) -> None:
        await _dispatch_write(self, None)


class _AccessoryUpdate(XtoolEntity, UpdateEntity):
    """Firmware-update entity for one BT/wired accessory.

    Not driven by an :class:`AccessoryEntitySpec` — the update
    entity is auto-built for every accessory whose definition
    carries a non-empty ``firmware_content_id``. Cloud-side state
    (latest version + release notes) is fetched on demand via the
    same single-package endpoint the laser firmware-update uses.

    Install action is gated by ``coordinator.enable_firmware_updates``;
    when disabled the entity stays in "read-only" mode (release
    notes + latest version visible, but no install).
    """

    _xtool_platform = "update"
    # ``has_entity_name = True`` lets HA resolve the display name
    # via ``_attr_translation_key`` — same pattern the rest of the
    # integration uses. Serial-prefixed entity-id stays intact via
    # ``suggested_object_id`` + the one-shot registry migration.
    _attr_has_entity_name = True
    _attr_device_class = UpdateDeviceClass.FIRMWARE

    def __init__(
        self,
        coordinator: XtoolCoordinator,
        definition: AccessoryDefinition,
        sn: str,
    ) -> None:
        super().__init__(coordinator)
        self._definition = definition
        self._sn = sn
        self._accessory_key = f"{definition.type_id}:{sn}"
        self._attr_translation_key = (
            f"accessory_{definition.type_id.lower()}_firmware"
        )
        self._set_unique_id(
            f"accessory_{definition.type_id.lower()}_{sn}_firmware"
        )
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

        self._update_info: FirmwareUpdateInfo | None = None
        self._latest_notes: str | None = None
        self._last_check: float = 0.0
        self._checked_once = False
        self._attr_in_progress: int | bool = False

        features = UpdateEntityFeature.RELEASE_NOTES
        if coordinator.enable_firmware_updates:
            features |= UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
        self._attr_supported_features = features

    @property
    def device_info(self) -> DeviceInfo:
        laser_sid = self.coordinator.serial_number
        is_synthetic_sn = not self._sn or self._sn.startswith("slot")
        if is_synthetic_sn:
            device_name = self._definition.friendly_name
            serial_for_info: str | None = None
        else:
            device_name = f"{self._definition.friendly_name} ({self._sn})"
            serial_for_info = self._sn
        return DeviceInfo(
            identifiers={(DOMAIN, f"{laser_sid}:{self._accessory_key}")},
            via_device=(DOMAIN, laser_sid),
            name=device_name,
            manufacturer="xTool",
            model=self._definition.friendly_name,
            serial_number=serial_for_info,
        )

    @property
    def _state(self) -> AccessoryState | None:
        data = self.coordinator.data
        if data is None:
            return None
        return data.connected_accessories.get(self._accessory_key)

    @property
    def available(self) -> bool:
        # Update entity stays available across laser-side outages so
        # the cloud release-notes / latest-version probe keeps
        # running. Only the accessory still being paired matters; the
        # install action is gated separately in ``async_install``.
        return self.coordinator.data is not None and self._state is not None

    @property
    def installed_version(self) -> str | None:
        s = self._state
        if s is None:
            return None
        version = s.fields.get("version")
        return str(version) if version else None

    @property
    def latest_version(self) -> str | None:
        if self._update_info:
            return self._update_info.latest_version
        return self.installed_version

    def release_notes(self) -> str | None:
        if self._update_info:
            return self._update_info.release_summary
        return self._latest_notes

    @property
    def release_summary(self) -> str | None:
        notes = (
            self._update_info.release_summary
            if self._update_info
            else self._latest_notes
        )
        if not notes:
            return None
        return notes[:255]

    async def async_added_to_hass(self) -> None:
        """Kick off the first cloud probe as soon as the accessory is live."""
        await super().async_added_to_hass()

        async def _check_for_update() -> None:
            if self._checked_once:
                return
            if self._state is None:
                return
            await self.async_update()
            self.async_write_ha_state()

        def _check_listener() -> None:
            self.hass.async_create_task(_check_for_update())

        self.async_on_remove(
            self.coordinator.async_add_listener(_check_listener)
        )
        if self._state is not None:
            await _check_for_update()

    async def async_update(self) -> None:
        now = time.monotonic()
        should_check = (
            not self._checked_once
            or (now - self._last_check >= self.coordinator.firmware_check_interval)
        )
        if not should_check:
            return
        if self._state is None:
            return
        current_version = self.installed_version or "0.0.0.0"
        content_id = self._definition.firmware_content_id
        if not content_id:
            return

        self._last_check = now
        self._checked_once = True

        self._update_info = await check_accessory_firmware_update(
            content_id=content_id,
            device_id=self.coordinator.serial_number,
            current_version=current_version,
        )
        if self._update_info is None:
            latest = await check_accessory_firmware_update(
                content_id=content_id,
                device_id=self.coordinator.serial_number,
                current_version=current_version,
                force_latest=True,
            )
            self._latest_notes = latest.release_summary if latest else None
        else:
            self._latest_notes = None

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        if not self.coordinator.enable_firmware_updates:
            raise HomeAssistantError(
                "Firmware updates are disabled. Enable them in the "
                "integration options."
            )
        if (
            self.coordinator.power_switch_is_off
            or self.coordinator.data is None
            or not self.coordinator.data.available
        ):
            raise HomeAssistantError(
                "Laser is offline — power it on and wait for it to "
                "reconnect before installing accessory firmware."
            )
        if not self._update_info or not self._update_info.files:
            _LOGGER.warning(
                "Accessory %s: no firmware files available to install",
                self._accessory_key,
            )
            return

        fw_file = self._update_info.files[0]
        total = max(fw_file.file_size, 1)

        def _set_progress(percent: int) -> None:
            percent = max(0, min(100, percent))
            if self._attr_in_progress != percent:
                self._attr_in_progress = percent
                self.async_write_ha_state()

        try:
            _set_progress(0)
            _LOGGER.info(
                "Accessory %s: downloading firmware %s (%d bytes)",
                self._accessory_key, fw_file.name, fw_file.file_size,
            )

            def _on_download(done: int, file_total: int) -> None:
                ref = file_total or fw_file.file_size or done
                ref = max(ref, 1)
                # 0-30 % = download
                _set_progress(int(min(done, ref) / ref * 30))

            blob = await download_firmware(
                fw_file.url,
                progress_cb=_on_download,
                expected_size=fw_file.file_size,
            )
            _set_progress(30)

            md5 = fw_file.md5 or hashlib.md5(blob).hexdigest()
            ext = fw_file.name.rsplit(".", 1)[-1] if "." in fw_file.name else "bin"
            upload_name = f"{md5}.{ext}"

            _LOGGER.info(
                "Accessory %s: uploading + flashing as %s",
                self._accessory_key, upload_name,
            )

            def _on_flash(f: float) -> None:
                # 30-100 % = upload + flash
                _set_progress(int(30 + 70 * max(0.0, min(1.0, f))))

            await self.coordinator.protocol.upload_accessory_firmware(
                accessory_type_id=self._definition.type_id,
                blob=blob,
                md5=md5,
                filename=upload_name,
                progress_cb=_on_flash,
            )

            _set_progress(100)
            _LOGGER.info(
                "Accessory %s: firmware update complete",
                self._accessory_key,
            )
            self._update_info = None
            self._checked_once = False  # Re-check after reboot

        except NotImplementedError as err:
            _LOGGER.error(
                "Accessory %s: firmware update not supported on this "
                "protocol family: %s",
                self._accessory_key, err,
            )
            raise HomeAssistantError(str(err)) from err
        except Exception as err:
            _LOGGER.error(
                "Accessory %s: firmware update failed: %s",
                self._accessory_key, err,
            )
            raise
        finally:
            self._attr_in_progress = False
            self.async_write_ha_state()


_PLATFORM_TO_CLASS: dict[str, type] = {
    "sensor": _AccessorySensor,
    "binary_sensor": _AccessoryBinarySensor,
    "switch": _AccessorySwitch,
    "select": _AccessorySelect,
    "number": _AccessoryNumber,
    "button": _AccessoryButton,
}


async def _passthrough_write(entity: _AccessoryEntity, mcode: str) -> None:
    """Send a write-side M-code through the right transport.

    Routes laser-host M-codes (``M15`` air-assist, ``M1099``
    delay, …) through ``protocol.send_command``; everything
    else through ``parts_control`` (WS-V2) or ``passthrough``
    (REST / D-series F0F7 tunnel). Families without a tunnel
    helper (S1) fall back to ``send_command`` for every write —
    their accessory M-codes (e.g. ``M9039`` for the AP2 gear)
    ride the same M-code WS as the laser's own commands.
    ``LASER_HOST_MCODES`` lives in :mod:`accessories.base`
    alongside the rest of the BT-accessory M-code constants.
    """
    proto = entity.coordinator.protocol
    head = mcode.split(" ", 1)[0]
    tunnel = getattr(proto, "parts_control", None) or getattr(
        proto, "passthrough", None,
    )
    if head in LASER_HOST_MCODES or tunnel is None:
        sender = getattr(proto, "send_command", None)
        if sender is None:
            _LOGGER.debug(
                "Accessory %s: no send_command on protocol",
                entity._accessory_key,
            )
            return
        try:
            await sender(mcode)
        except Exception as err:
            _LOGGER.debug(
                "Accessory %s send_command %s failed: %s",
                entity._accessory_key, mcode, err,
            )
        return
    try:
        await tunnel(mcode, entity._definition.prefix)
    except Exception as err:
        _LOGGER.debug(
            "Accessory %s passthrough write %s failed: %s",
            entity._accessory_key, mcode, err,
        )


def build_accessory_entities(
    coordinator: XtoolCoordinator,
    accessory: AccessoryState,
) -> list[Any]:
    """Build the per-platform entity set for one connected accessory.

    Specs whose ``field`` isn't present in the accessory's parsed
    fields dict are skipped — keeps cross-variant definitions
    (e.g. the unified Purifier spec) from registering entities
    the device's wire shape doesn't actually carry. Specs without
    a ``field`` (action buttons, write-only selects) always build.

    An ``UpdateEntity`` is added automatically for every definition
    that declares a non-empty ``firmware_content_id`` — accessory
    firmware lives in the same cloud bundles as the laser firmware,
    so the same cloud-check helper covers both.
    """
    definition = get_definition(accessory.type_id)
    if definition is None:
        return []
    out: list[Any] = []
    fields = accessory.fields
    has_update_entity = bool(definition.firmware_content_id)
    for spec in definition.entities:
        cls = _PLATFORM_TO_CLASS.get(spec.platform)
        if cls is None:
            continue
        if spec.field is not None and spec.field not in fields:
            continue
        # When an update entity surfaces the firmware version, suppress
        # the redundant ``version`` sensor — the user already sees the
        # installed version on the update entity itself.
        if has_update_entity and spec.platform == "sensor" and spec.field == "version":
            continue
        out.append(cls(coordinator, definition, accessory.sn, spec))
    if has_update_entity:
        out.append(_AccessoryUpdate(coordinator, definition, accessory.sn))
    return out
