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


def _md_hardbreaks(text: str) -> str:
    """Convert plain text into markdown that preserves line breaks.

    The cloud release-note descriptions arrive with single ``\\n`` between
    lines. Markdown collapses single newlines into spaces — only blank
    lines (``\\n\\n``) start a new paragraph — so the dialog rendering
    drops the breaks. Append two trailing spaces before each lone newline
    to force a markdown hard break.
    """
    if not text:
        return text
    out: list[str] = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        out.append(line)
        if i < len(lines) - 1:
            # If the next line is empty, leave it as a paragraph break.
            # Otherwise insert a hard break (two spaces + newline).
            nxt = lines[i + 1]
            if nxt.strip() == "":
                out.append("\n")
            else:
                out.append("  \n")
    return "".join(out)


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
    force_latest: bool = False,
) -> FirmwareUpdateInfo | None:
    """Check the xTool cloud API for available firmware updates.

    Args:
        content_id: Firmware content ID (e.g. "xTool-d2-firmware" for S1)
        device_id: Device serial number
        current_versions: Map of board_id -> current firmware version string
            For multi-package: {"xTool-d2-0x20": "V40.32.015.2025.01", ...}
            For single-package: {"main": "1.0.0"}
        multi_package: True for S1 (multiple boards), False for REST models
        force_latest: When True, queries the API with a baseline version
            ``0.0.0.0`` so the cloud always returns the latest release info.
            Used by the Update entity to surface release notes for the
            currently-installed version even when no real update is available.

    Returns:
        FirmwareUpdateInfo if update available (or ``force_latest=True`` and
        the API has a release on file), None otherwise.
    """
    if not content_id:
        return None

    if force_latest:
        # Pretend every board is on a baseline version so the API never
        # short-circuits with "you're on the latest".
        current_versions = {k: "0.0.0.0" for k in current_versions} or {"main": "0.0.0.0"}

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
        "domain": "atomm",
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
    notes = []
    versions = []
    board_versions: dict[str, str] = {}
    total_size = 0

    # S1 board ID to burnType mapping
    burn_types = {"xTool-d2-0x20": "1", "xTool-d2-0x21": "2", "xTool-d2-0x22": "3"}

    # Per-board labels for the changelog header
    board_labels = {
        "xTool-d2-0x20": "Main MCU",
        "xTool-d2-0x21": "Laser MCU",
        "xTool-d2-0x22": "WiFi MCU",
    }

    for entry in data:
        board_id = entry.get("id", "")
        version = entry.get("version", "")
        contents = entry.get("contents", [])
        title = entry.get("title", {}).get("en", "")
        desc = entry.get("description", {}).get("en", "")

        versions.append(version)
        if board_id and version:
            board_versions[board_id] = version

        # Build per-board changelog block — board label · version · title · description
        label = board_labels.get(board_id, board_id)
        header_parts = [f"### {label}"]
        if version:
            header_parts.append(f"v{version}")
        if title and title != version:
            header_parts.append(title)
        block = " · ".join(header_parts)
        if desc:
            block += f"\n\n{_md_hardbreaks(desc)}"
        notes.append(block)

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
        release_summary="\n\n".join(notes),
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
        "domain": "atomm",
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

    title = data.get("title", {}).get("en", "")
    desc = data.get("description", {}).get("en", "")
    version = data.get("version", "")

    # Build "title · version" header followed by the description body
    header_parts: list[str] = []
    if title and title != version:
        header_parts.append(title)
    if version:
        header_parts.append(f"v{version}")
    header = " · ".join(header_parts)
    desc_md = _md_hardbreaks(desc)
    if header and desc_md:
        release_summary = f"### {header}\n\n{desc_md}"
    else:
        release_summary = desc_md or header

    # Some models (e.g. M1) deliver multi-content firmware: a small ``.script``
    # payload followed by the main ``.bin`` blob. The flash strategy expects
    # script-first ordering — sort defensively in case the cloud returns
    # them out of order.
    def _flash_sort_key(c: dict) -> int:
        name = (c.get("name") or "").lower()
        if name.endswith(".script"):
            return 0
        if name.endswith(".bin"):
            return 1
        return 2

    files = [
        FirmwareFile(
            board_id=content_id,
            name=c.get("name", ""),
            url=c.get("url", ""),
            md5=c.get("md5", ""),
            file_size=c.get("fileSize", 0),
        )
        for c in sorted(contents, key=_flash_sort_key)
    ]

    return FirmwareUpdateInfo(
        latest_version=version,
        release_summary=release_summary,
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
