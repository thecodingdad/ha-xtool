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
from .const import FIRMWARE_CHECK_INTERVAL
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
        if self._update_info:
            return self._update_info.release_summary
        return None

    async def async_update(self) -> None:
        """Periodic cloud-side update check (every 6 h, plus first availability)."""
        now = time.monotonic()
        should_check = (
            not self._checked_once
            or (now - self._last_check >= FIRMWARE_CHECK_INTERVAL)
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
        completed_bytes = 0

        def _set_progress(percent: int) -> None:
            percent = max(0, min(100, percent))
            if self._attr_in_progress != percent:
                self._attr_in_progress = percent
                self.async_write_ha_state()

        try:
            _set_progress(0)
            for fw_file in self._update_info.files:
                file_share = fw_file.file_size / total
                base_pct = (completed_bytes / total) * 100
                # Each file: download = first 30 % of its share, flash = remaining 70 %
                download_end_pct = base_pct + file_share * 30
                flash_end_pct = base_pct + file_share * 100

                _LOGGER.info(
                    "Downloading firmware: %s (%d bytes)",
                    fw_file.name, fw_file.file_size,
                )

                def _on_download(done: int, file_total: int) -> None:
                    file_bytes = file_total or fw_file.file_size or done
                    file_bytes = max(file_bytes, 1)
                    frac = min(done, file_bytes) / file_bytes
                    _set_progress(int(base_pct + (download_end_pct - base_pct) * frac))

                data = await download_firmware(
                    fw_file.url,
                    progress_cb=_on_download,
                    expected_size=fw_file.file_size,
                )
                _set_progress(int(download_end_pct))

                _LOGGER.info("Flashing firmware: %s", fw_file.name)

                def _on_flash_progress(f: float) -> None:
                    _set_progress(
                        int(download_end_pct + (flash_end_pct - download_end_pct) * f)
                    )

                await self.coordinator.protocol.flash_firmware(
                    fw_file, data, progress_cb=_on_flash_progress
                )

                completed_bytes += fw_file.file_size
                _set_progress(int(flash_end_pct))

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
