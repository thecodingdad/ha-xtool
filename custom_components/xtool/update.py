"""Firmware update entity — base orchestrator. Per-family subclasses live in entities.py."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity
from .firmware import (
    FirmwareUpdateInfo,
    check_firmware_update,
    download_firmware,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool firmware update entity via per-family builder."""
    coordinator = entry.runtime_data
    entities = list(coordinator.build_updates())
    if entities:
        async_add_entities(entities)


class XtoolFirmwareUpdate(XtoolEntity, UpdateEntity):
    """Base firmware update entity — single-package, single-version display.

    Per-family subclasses override ``installed_version`` / ``latest_version``
    for richer rendering (e.g. S1's three-board composite) or override
    behavioural hooks for family-specific flash setup.
    """

    _attr_translation_key = "firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_firmware_update"
        self._update_info: FirmwareUpdateInfo | None = None
        # Release notes for the latest published version, fetched even when
        # no update is available so the user can see what's in the version
        # they are currently on (or the next one).
        self._latest_notes: str | None = None
        self._last_check: float = 0.0
        self._checked_once = False
        self._attr_in_progress: int | bool = False

        features = UpdateEntityFeature.RELEASE_NOTES
        if coordinator.enable_firmware_updates:
            features |= UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
        self._attr_supported_features = features

    @property
    def installed_version(self) -> str | None:
        """Default: single firmware string from the coordinator."""
        return self.coordinator.firmware_version or None

    @property
    def latest_version(self) -> str | None:
        """Default: latest_version from cloud check, fall back to installed."""
        if self._update_info:
            return self._update_info.latest_version
        return self.installed_version

    def release_notes(self) -> str | None:
        # When an update is available we already have the notes from the
        # version-comparison cloud call. When no update is available we fall
        # back to the latest-release probe (`force_latest=True`) so users
        # can still read what changed in their installed firmware.
        if self._update_info:
            return self._update_info.release_summary
        return self._latest_notes

    @property
    def release_summary(self) -> str | None:
        # State attribute mirrors release_notes() so the markdown is visible
        # in the entity's "more info" dialog even when state is "off"
        # (no update available). HA truncates long values automatically.
        notes = self._update_info.release_summary if self._update_info else self._latest_notes
        if not notes:
            return None
        # HA caps release_summary at 255 chars
        return notes[:255]

    async def async_added_to_hass(self) -> None:
        """Kick off the first cloud check as soon as the device is reachable.

        HA's default UpdateEntity scan interval is 30 minutes, so without
        this nudge release notes would disappear for half an hour after
        every HA restart. We listen for the first available coordinator
        update and trigger a state refresh, which calls ``async_update``
        and runs the cloud probe.
        """
        await super().async_added_to_hass()

        async def _kick(*_: Any) -> None:
            if self._checked_once:
                return
            if self.coordinator.data is None or not self.coordinator.data.available:
                return
            await self.async_update()
            self.async_write_ha_state()

        self.async_on_remove(self.coordinator.async_add_listener(_kick))
        # Also try immediately in case data is already available.
        if self.coordinator.data and self.coordinator.data.available:
            await _kick()

    async def async_update(self) -> None:
        """Periodic cloud-side update check (every 6 h, plus first availability)."""
        now = time.monotonic()
        should_check = (
            not self._checked_once
            or (now - self._last_check >= self.coordinator.firmware_check_interval)
        )
        if not should_check:
            return
        if self.coordinator.data is None or not self.coordinator.data.available:
            return

        self._last_check = now
        self._checked_once = True

        model = self.coordinator.model
        current_versions = await self.coordinator.protocol.get_firmware_versions(
            self.coordinator
        )
        if not current_versions:
            return

        self._update_info = await check_firmware_update(
            content_id=model.firmware_content_id,
            device_id=self.coordinator.serial_number,
            current_versions=current_versions,
            multi_package=model.firmware_multi_package,
        )

        # Even when there's no update, fetch the latest release info so the
        # entity can render release notes for what's installed.
        if self._update_info is None:
            latest = await check_firmware_update(
                content_id=model.firmware_content_id,
                device_id=self.coordinator.serial_number,
                current_versions=current_versions,
                multi_package=model.firmware_multi_package,
                force_latest=True,
            )
            self._latest_notes = latest.release_summary if latest else None
        else:
            # When an update is available the notes from the primary check
            # already cover the new version; clear the standalone fallback.
            self._latest_notes = None

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Generic install: download each file, hand to protocol.flash_firmware."""
        if not self.coordinator.enable_firmware_updates:
            raise HomeAssistantError(
                "Firmware updates are disabled. Enable them in the integration options."
            )
        if not self._update_info or not self._update_info.files:
            _LOGGER.warning("No firmware update files available to install")
            return

        _LOGGER.info(
            "Starting firmware update to %s (%d files, %d bytes)",
            self._update_info.latest_version,
            len(self._update_info.files),
            self._update_info.total_size,
        )

        total = max(self._update_info.total_size, 1)
        download_phase_pct = 30  # 0–30 % = download, 30–100 % = flash
        completed_bytes = 0

        def _set_progress(percent: int) -> None:
            percent = max(0, min(100, percent))
            if self._attr_in_progress != percent:
                self._attr_in_progress = percent
                self.async_write_ha_state()

        try:
            _set_progress(0)
            blobs: list[bytes] = []
            for fw_file in self._update_info.files:
                base_pct = (completed_bytes / total) * download_phase_pct
                file_share_pct = (fw_file.file_size / total) * download_phase_pct

                _LOGGER.info(
                    "Downloading firmware: %s (%d bytes)",
                    fw_file.name, fw_file.file_size,
                )

                def _on_download(done: int, file_total: int) -> None:
                    file_bytes = file_total or fw_file.file_size or done
                    file_bytes = max(file_bytes, 1)
                    frac = min(done, file_bytes) / file_bytes
                    _set_progress(int(base_pct + file_share_pct * frac))

                data = await download_firmware(
                    fw_file.url,
                    progress_cb=_on_download,
                    expected_size=fw_file.file_size,
                )
                blobs.append(data)
                completed_bytes += fw_file.file_size

            _set_progress(download_phase_pct)
            _LOGGER.info("Flashing firmware (%d file(s))", len(blobs))

            def _on_flash_progress(f: float) -> None:
                _set_progress(
                    int(download_phase_pct + (100 - download_phase_pct) * f)
                )

            await self.coordinator.protocol.flash_firmware(
                self._update_info.files,
                blobs,
                progress_cb=_on_flash_progress,
            )

            _LOGGER.info("Firmware update complete — device will reboot")
            _set_progress(100)
            self._update_info = None
            self._checked_once = False  # Re-check after reboot

        except Exception as err:
            _LOGGER.error("Firmware update failed: %s", err)
            raise
        finally:
            self._attr_in_progress = False
            self.async_write_ha_state()
