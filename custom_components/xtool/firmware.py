"""xTool firmware update cloud-API client.

Speaks to ``api.xtool.com``; protocol-specific flashing lives on each
``XtoolProtocol`` implementation (see ``protocols/base.py FirmwareFile`` /
``flash_firmware``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

import aiohttp

from .const import FIRMWARE_API_BASE
from .protocols.base import FirmwareFile, FirmwareUpdateInfo  # re-exported

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "FirmwareFile",
    "FirmwareUpdateInfo",
    "check_firmware_update",
    "download_firmware",
    "parse_firmware_version",
]


def parse_firmware_version(raw: str) -> str:
    """Parse firmware version string to API-compatible format.

    The cloud API expects versions as dot-separated numbers without prefix.
    Example: "V40.32.015.2025.01" → "40.32.15.1"
    Extracts all digit groups, keeps first 3 + last, joins with dots.
    """
    digits = re.findall(r"\d+", raw)
    if not digits:
        return raw
    # Keep first 3 groups + last group (skip middle ones)
    parts = []
    for i, d in enumerate(digits):
        if i <= 2 or i == len(digits) - 1:
            parts.append(str(int(d)))  # int() strips leading zeros
    return ".".join(parts)


async def check_firmware_update(
    content_id: str,
    device_id: str,
    current_versions: dict[str, str],
    multi_package: bool = False,
) -> FirmwareUpdateInfo | None:
    """Check the xTool cloud API for available firmware updates.

    Args:
        content_id: Firmware content ID (e.g. "xcs-d2-firmware" for S1)
        device_id: Device serial number
        current_versions: Map of board_id -> current firmware version string
            For multi-package: {"xcs-d2-0x20": "V40.32.015.2025.01", ...}
            For single-package: {"main": "1.0.0"}
        multi_package: True for S1 (multiple boards), False for REST models

    Returns:
        FirmwareUpdateInfo if update available, None otherwise.
    """
    if not content_id:
        return None

    try:
        if multi_package:
            return await _check_multi_package(content_id, device_id, current_versions)
        return await _check_single_package(content_id, device_id, current_versions)
    except Exception as err:
        _LOGGER.debug("Firmware update check failed: %s", err)
        return None


async def _check_multi_package(
    content_id: str,
    device_id: str,
    current_versions: dict[str, str],
) -> FirmwareUpdateInfo | None:
    """Check multi-package firmware update (S1 with multiple boards)."""
    packages = [
        {"contentId": board_id, "contentVersion": parse_firmware_version(version)}
        for board_id, version in current_versions.items()
    ]
    payload = {
        "domain": "xcs",
        "region": "en",
        "contentId": content_id,
        "deviceId": device_id,
        "packages": packages,
    }

    url = f"{FIRMWARE_API_BASE}/packages/version/latest"
    data = await _api_post(url, payload)
    if not data or not isinstance(data, list) or len(data) == 0:
        return None

    # Build update info from response
    files = []
    descriptions = []
    versions = []
    board_versions: dict[str, str] = {}
    total_size = 0

    # S1 board ID to burnType mapping
    burn_types = {"xcs-d2-0x20": "1", "xcs-d2-0x21": "2", "xcs-d2-0x22": "3"}

    for entry in data:
        board_id = entry.get("id", "")
        version = entry.get("version", "")
        contents = entry.get("contents", [])
        desc = entry.get("description", {})

        versions.append(version)
        if board_id and version:
            board_versions[board_id] = version
        if desc.get("en"):
            descriptions.append(desc["en"])

        for content in contents:
            files.append(FirmwareFile(
                board_id=board_id,
                name=content.get("name", ""),
                url=content.get("url", ""),
                md5=content.get("md5", ""),
                file_size=content.get("fileSize", 0),
                burn_type=burn_types.get(board_id, ""),
            ))
            total_size += content.get("fileSize", 0)

    return FirmwareUpdateInfo(
        latest_version=", ".join(versions) if len(versions) > 1 else versions[0],
        release_summary="\n\n".join(descriptions),
        files=files,
        total_size=total_size,
        board_versions=board_versions,
    )


async def _check_single_package(
    content_id: str,
    device_id: str,
    current_versions: dict[str, str],
) -> FirmwareUpdateInfo | None:
    """Check single-package firmware update (REST models)."""
    version = next(iter(current_versions.values()), "")
    payload = {
        "domain": "xcs",
        "region": "en",
        "contentId": content_id,
        "deviceId": device_id,
        "contentVersion": parse_firmware_version(version),
    }

    url = f"{FIRMWARE_API_BASE}/package/version/latest"
    data = await _api_post(url, payload)
    if not data or not isinstance(data, dict):
        return None

    contents = data.get("contents", [])
    if not contents:
        return None

    desc = data.get("description", {})
    files = [
        FirmwareFile(
            board_id=content_id,
            name=c.get("name", ""),
            url=c.get("url", ""),
            md5=c.get("md5", ""),
            file_size=c.get("fileSize", 0),
        )
        for c in contents
    ]

    return FirmwareUpdateInfo(
        latest_version=data.get("version", ""),
        release_summary=desc.get("en", ""),
        files=files,
        total_size=sum(f.file_size for f in files),
    )


async def download_firmware(
    url: str,
    progress_cb: Callable[[int, int], None] | None = None,
    expected_size: int = 0,
) -> bytes:
    """Download a firmware file from the xTool cloud.

    If progress_cb is given, it is called with (downloaded_bytes, total_bytes)
    as each chunk arrives. Total is taken from Content-Length, falling back to
    expected_size when the header is missing.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Firmware download failed: HTTP {resp.status}")
            total = resp.content_length or expected_size
            if progress_cb is None:
                return await resp.read()
            buf = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                buf.extend(chunk)
                progress_cb(len(buf), total)
            return bytes(buf)


async def _api_post(url: str, payload: dict) -> dict | list | None:
    """POST to the xTool cloud API and return the data field."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return None
                result = await resp.json()
                if result.get("code") != 0:
                    _LOGGER.debug("Firmware API error: %s", result.get("message"))
                    return None
                return result.get("data")
    except Exception as err:
        _LOGGER.debug("Firmware API request failed: %s", err)
        return None
