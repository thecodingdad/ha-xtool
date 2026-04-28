"""Firmware update entity for xTool Laser integration."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import XtoolConfigEntry
from .const import (
    CMD_ENTER_UPGRADE_MODE,
    DEFAULT_HTTP_PORT,
    FIRMWARE_CHECK_INTERVAL,
    HTTP_ACTION_UPGRADE_PROGRESS,
    HTTP_PATH_BURN,
    HTTP_PATH_UPGRADE,
)
from .coordinator import XtoolCoordinator
from .entity import XtoolEntity
from .firmware import (
    FirmwareUpdateInfo,
    check_firmware_update,
    download_firmware,
)

_LOGGER = logging.getLogger(__name__)

# Display labels for S1 board IDs in the composite version string
_BOARD_LABELS = {
    "xcs-d2-0x20": "Main",
    "xcs-d2-0x21": "Laser",
    "xcs-d2-0x22": "WiFi",
}


def _short_version(raw: str) -> str:
    """Strip the 'V' prefix and any trailing build suffix for compact display."""
    if not raw:
        return "?"
    # Drop leading V/v
    v = raw.lstrip("Vv")
    # Cut off after first space (build suffixes like " B10")
    return v.split()[0] if v else "?"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XtoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up xTool firmware update entity."""
    coordinator = entry.runtime_data
    if coordinator.model.firmware_content_id:
        async_add_entities([XtoolFirmwareUpdate(coordinator)])


class XtoolFirmwareUpdate(XtoolEntity, UpdateEntity):
    """Firmware update entity for xTool devices."""

    _attr_translation_key = "firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE

    def __init__(self, coordinator: XtoolCoordinator) -> None:
        """Initialize the firmware update entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.serial_number}_firmware_update"
        self._update_info: FirmwareUpdateInfo | None = None
        self._last_check: float = 0.0
        self._checked_once = False
        self._attr_in_progress: int | bool = False

        features = UpdateEntityFeature.RELEASE_NOTES
        if coordinator.enable_firmware_updates:
            features |= (
                UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
            )
        self._attr_supported_features = features

    @property
    def installed_version(self) -> str | None:
        """Return installed firmware version (composite for multi-board models)."""
        model = self.coordinator.model
        if model.firmware_multi_package:
            return self._build_composite(self._current_board_versions())
        return self.coordinator.firmware_version or None

    @property
    def latest_version(self) -> str | None:
        """Return latest firmware version (composite for multi-board models)."""
        model = self.coordinator.model
        if model.firmware_multi_package:
            current = self._current_board_versions()
            new = dict(current)
            if self._update_info and self._update_info.board_versions:
                new.update(self._update_info.board_versions)
            return self._build_composite(new)
        if self._update_info:
            return self._update_info.latest_version
        return self.installed_version

    def _current_board_versions(self) -> dict[str, str]:
        """Per-board installed versions for multi-package models."""
        model = self.coordinator.model
        if not (model.firmware_multi_package and model.firmware_board_ids):
            return {}
        ids = model.firmware_board_ids
        # Order: 0x20 main, 0x21 laser, 0x22 wifi (per S1 board map)
        sources = [
            self.coordinator.firmware_version,  # main MCU (M99)
            self.coordinator.laser_firmware,    # laser MCU (M1199)
            self.coordinator.wifi_firmware,     # ESP32 (M2099)
        ]
        return {
            board_id: src
            for board_id, src in zip(ids, sources)
            if src
        }

    def _build_composite(self, versions: dict[str, str]) -> str | None:
        """Render '<Label> <version> / <Label> <version> / ...' for the entity."""
        if not versions:
            return None
        parts = []
        for board_id, ver in versions.items():
            label = _BOARD_LABELS.get(board_id, board_id)
            parts.append(f"{label} {_short_version(ver)}")
        return " / ".join(parts)

    def release_notes(self) -> str | None:
        """Return release notes for the latest version."""
        if self._update_info:
            return self._update_info.release_summary
        return None

    async def async_update(self) -> None:
        """Check for firmware updates periodically."""
        now = time.monotonic()

        # Check on first availability (device just turned on) and then every 6 hours
        should_check = (
            not self._checked_once
            or (now - self._last_check >= FIRMWARE_CHECK_INTERVAL)
        )

        if not should_check:
            return

        # Only check when device is available
        if self.coordinator.data is None or not self.coordinator.data.available:
            return

        self._last_check = now
        self._checked_once = True

        model = self.coordinator.model
        current_versions = self._get_current_versions()
        if not current_versions:
            return

        self._update_info = await check_firmware_update(
            content_id=model.firmware_content_id,
            device_id=self.coordinator.serial_number,
            current_versions=current_versions,
            multi_package=model.firmware_multi_package,
        )

    def _get_current_versions(self) -> dict[str, str]:
        """Build current firmware version map for the API call."""
        model = self.coordinator.model

        if model.firmware_multi_package and model.firmware_board_ids:
            # S1: need per-board versions from M2003 response
            # M99 = main, M1199 = laser, M2099 = wifi
            versions = {}
            info = self.coordinator.data
            if info is None:
                return {}

            # Try to get per-board versions from the device info
            # These are stored during _fetch_device_info from M2003
            fw = self.coordinator.firmware_version
            if fw and len(model.firmware_board_ids) > 0:
                # Use the main firmware version for the first board
                # and try to get others from stored device info
                versions[model.firmware_board_ids[0]] = fw

                # For additional boards, check if we have laser/wifi firmware
                from .protocol import DeviceInfo

                if hasattr(self.coordinator, '_device_info_cache'):
                    di = self.coordinator._device_info_cache
                    if len(model.firmware_board_ids) > 1 and di.laser_firmware:
                        versions[model.firmware_board_ids[1]] = di.laser_firmware
                    if len(model.firmware_board_ids) > 2 and di.wifi_firmware:
                        versions[model.firmware_board_ids[2]] = di.wifi_firmware

                # Fill missing with main version
                for board_id in model.firmware_board_ids:
                    if board_id not in versions:
                        versions[board_id] = fw

            return versions

        # Single-package: just use the main firmware version
        return {"main": self.coordinator.firmware_version} if self.coordinator.firmware_version else {}

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install a firmware update."""
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

        host = self.coordinator.host
        model = self.coordinator.model
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
                    fw_file.name,
                    fw_file.file_size,
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
                if model.firmware_multi_package:
                    await self._flash_s1(
                        host,
                        data,
                        fw_file.burn_type,
                        flash_progress_cb=lambda f: _set_progress(
                            int(download_end_pct + (flash_end_pct - download_end_pct) * f)
                        ),
                    )
                else:
                    await self._flash_rest(host, data, fw_file.name)

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

    async def _flash_s1(
        self,
        host: str,
        data: bytes,
        burn_type: str,
        flash_progress_cb: Any = None,
    ) -> None:
        """Flash firmware on S1 via /burn endpoint with real progress polling."""
        # Enter upgrade mode
        await self.coordinator.send_command(CMD_ENTER_UPGRADE_MODE)

        url = f"http://{host}:{DEFAULT_HTTP_PORT}{HTTP_PATH_BURN}"
        form = aiohttp.FormData()
        form.add_field("file", data, filename="mcu_firmware.bin", content_type="application/octet-stream")
        form.add_field("burnType", burn_type)

        async with aiohttp.ClientSession() as session:
            # Run upload and progress poll concurrently
            async def _upload() -> None:
                async with session.post(
                    url, data=form, timeout=aiohttp.ClientTimeout(total=600)
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Firmware upload failed: HTTP {resp.status}")
                    _LOGGER.debug("Firmware upload response: %s", await resp.text())

            poll_task = asyncio.create_task(
                self._poll_flash_progress(host, flash_progress_cb)
            )
            try:
                await _upload()
            finally:
                poll_task.cancel()
                try:
                    await poll_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _poll_flash_progress(
        self, host: str, progress_cb: Any
    ) -> None:
        """Poll /system?action=get_upgrade_progress and report real progress."""
        if progress_cb is None:
            return
        url = (
            f"http://{host}:{DEFAULT_HTTP_PORT}"
            f"/system?action={HTTP_ACTION_UPGRADE_PROGRESS}"
        )
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            payload = await resp.json(content_type=None)
                            curr = int(payload.get("curr_progress", 0))
                            total = int(payload.get("total_progress", 0))
                            if total > 0:
                                progress_cb(min(curr / total, 1.0))
                                if curr >= total:
                                    return
                except Exception as err:
                    _LOGGER.debug("Flash progress poll error: %s", err)
                await asyncio.sleep(2.0)

    async def _flash_rest(self, host: str, data: bytes, filename: str) -> None:
        """Flash firmware on REST models — try /upgrade single-blob, fall back to /package."""
        url_upgrade = f"http://{host}:{DEFAULT_HTTP_PORT}{HTTP_PATH_UPGRADE}"
        async with aiohttp.ClientSession() as session:
            # Path 1: simple multipart upload to /upgrade (no handshake)
            form = aiohttp.FormData()
            form.add_field("file", data, filename=filename, content_type="application/octet-stream")
            try:
                async with session.post(
                    url_upgrade, data=form, timeout=aiohttp.ClientTimeout(total=600)
                ) as resp:
                    if resp.status == 200:
                        _LOGGER.debug("Firmware upload via /upgrade succeeded")
                        return
                    _LOGGER.debug("/upgrade returned HTTP %s, falling back", resp.status)
            except Exception as err:
                _LOGGER.debug("/upgrade failed (%s), falling back to /package", err)

            # Path 2: legacy handshake + /package
            url_handshake = f"http://{host}:{DEFAULT_HTTP_PORT}/upgrade_version"
            async with session.post(
                url_handshake,
                json={"filename": filename, "fileSize": len(data)},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Firmware handshake failed: HTTP {resp.status}")

            url_upload = f"http://{host}:{DEFAULT_HTTP_PORT}/package"
            form = aiohttp.FormData()
            form.add_field("file", data, filename=filename, content_type="application/octet-stream")
            async with session.post(
                url_upload, data=form, timeout=aiohttp.ClientTimeout(total=600)
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Firmware upload failed: HTTP {resp.status}")
