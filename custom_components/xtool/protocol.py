"""Protocol abstraction and data classes for xTool laser devices.

This module defines the abstract protocol interface and shared data classes.
Concrete implementations are in ws_protocol.py, rest_protocol.py, and
http_mcode_protocol.py.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from .const import (
    CMD_FULL_INFO,
    DEFAULT_DEVICE_NAME,
    INFO_KEY_ACCESSORIES,
    INFO_KEY_DEVICE_NAME,
    INFO_KEY_LASER_FIRMWARE,
    INFO_KEY_MAIN_FIRMWARE,
    INFO_KEY_POWER_INFO,
    INFO_KEY_SERIAL_NUMBER,
    INFO_KEY_WIFI_FIRMWARE,
)

_LOGGER = logging.getLogger(__name__)

# Regex to extract parameter values from M-code responses
# e.g. "M13 A70 B70" -> {"A": "70", "B": "70"}
_PARAM_RE = re.compile(r"([A-Z])(-?[\d.]+)")


# --- Protocol Family ---


class ProtocolType(StrEnum):
    """Protocol families used by different xTool models."""

    WS_MCODE = "ws_mcode"  # S1: WebSocket M-codes on port 8081
    HTTP_MCODE = "http_mcode"  # D1/D1Pro/D1Pro2: HTTP POST M-codes on port 8080
    REST = "rest"  # F1/P2/M1/P1 etc: REST API on port 8080


# --- Data Classes ---


@dataclass
class LaserInfo:
    """Parsed laser module info from M116 power info string.

    Format: "X{type}Y{watts}B{producer}P{process_type}L{laser_tube}"
    """

    laser_type: int = 0
    power_watts: int = 0
    laser_producer: int = 0
    process_type: int = 0
    laser_tube: int = 0

    @property
    def type_name(self) -> str:
        """Human-readable laser type name."""
        from .const import get_laser_type_name

        return get_laser_type_name(self.laser_type, self.power_watts)

    @property
    def description(self) -> str:
        """Human-readable summary string for the sensor."""
        if self.power_watts == 0:
            return "Not detected"
        return f"{self.power_watts}W {self.type_name}"


@dataclass
class DeviceInfo:
    """Parsed device information from any protocol."""

    serial_number: str = ""
    device_name: str = ""
    laser: LaserInfo = field(default_factory=LaserInfo)
    main_firmware: str = ""
    laser_firmware: str = ""
    wifi_firmware: str = ""
    accessories: list[str] = field(default_factory=list)
    # Workspace dimensions in mm (M223 response on S1, static per model)
    workspace_x: float = 0.0
    workspace_y: float = 0.0
    workspace_z: float = 0.0

    @property
    def laser_power_watts(self) -> int:
        """Convenience accessor for laser power."""
        return self.laser.power_watts


@dataclass
class ConnectionInfo:
    """Device info gathered during connection validation."""

    host: str = ""
    name: str = ""
    serial_number: str = ""
    firmware_version: str = ""
    laser_power_watts: int = 0
    protocol_type: ProtocolType = ProtocolType.WS_MCODE
    device_info: DeviceInfo | None = None


# --- Parse Helpers (shared across protocol implementations) ---


def parse_params(response: str) -> dict[str, str]:
    """Parse M-code response parameters into a dict.

    Example: "M13 A70 B70" -> {"A": "70", "B": "70"}
    """
    return dict(_PARAM_RE.findall(response))


def parse_param_int(response: str, key: str, default: int = 0) -> int:
    """Extract a single integer parameter from response."""
    params = parse_params(response)
    try:
        return int(params.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def parse_param_float(response: str, key: str, default: float = 0.0) -> float:
    """Extract a single float parameter from response."""
    params = parse_params(response)
    try:
        return float(params.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def parse_quoted_string(response: str) -> str | None:
    """Extract a quoted string from response. E.g. 'M100 "xTool S1"' -> 'xTool S1'."""
    match = re.search(r'"([^"]*)"', response)
    return match.group(1) if match else None


def parse_m2003(raw: str) -> DeviceInfo:
    """Parse M2003 response into a structured DeviceInfo.

    The M2003 response contains a JSON object keyed by M-code numbers.
    Example: M2003{"M310":"MXDK...","M116":"X0Y20B1P1L3",...}
    """
    info = DeviceInfo()

    json_str = raw.replace(CMD_FULL_INFO, "", 1).strip()
    if not json_str:
        return info

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        _LOGGER.debug("Failed to parse M2003 JSON: %s", json_str)
        return info

    info.serial_number = data.get(INFO_KEY_SERIAL_NUMBER, "")
    info.device_name = data.get(INFO_KEY_DEVICE_NAME, "")
    info.main_firmware = data.get(INFO_KEY_MAIN_FIRMWARE, "")
    info.laser_firmware = data.get(INFO_KEY_LASER_FIRMWARE, "")
    info.wifi_firmware = data.get(INFO_KEY_WIFI_FIRMWARE, "")
    info.accessories = data.get(INFO_KEY_ACCESSORIES, [])
    info.laser = parse_laser_info(data.get(INFO_KEY_POWER_INFO, ""))

    return info


def parse_laser_info(raw: str) -> LaserInfo:
    """Parse M116 power info string into LaserInfo.

    Format: "X{type}Y{watts}B{producer}P{process_type}L{laser_tube}"
    Example: "X0Y20B1P1L3" -> type=0, watts=20, producer=1, process_type=1, laser_tube=3
    """
    laser = LaserInfo()
    if not raw:
        return laser

    params = dict(_PARAM_RE.findall(raw))
    for key, attr in (
        ("X", "laser_type"),
        ("Y", "power_watts"),
        ("B", "laser_producer"),
        ("P", "process_type"),
        ("L", "laser_tube"),
    ):
        try:
            setattr(laser, attr, int(params.get(key, "0")))
        except ValueError:
            pass

    return laser


def parse_workspace_dims(raw: str) -> tuple[float, float, float]:
    """Parse M223 response into workspace dimensions in mm.

    Example: "M223 X498.00 Y330.00 Z58.00" -> (498.0, 330.0, 58.0)
    """
    return (
        parse_param_float(raw, "X"),
        parse_param_float(raw, "Y"),
        parse_param_float(raw, "Z"),
    )


def parse_accessories(raw: str) -> list[str]:
    """Parse M1098 response into a list of firmware version strings.

    Input:  'M1098 "","","V40.208.003.3D28.01 B1","","",...'
    Output: ["", "", "V40.208.003.3D28.01 B1", "", ...]
    """
    content = raw.replace("M1098", "", 1).strip()
    if not content:
        return []
    return [part.strip().strip('"') for part in content.split(",")]


# --- Abstract Protocol ---


class XtoolProtocol(ABC):
    """Abstract base class for xTool device communication protocols."""

    def __init__(self, host: str) -> None:
        """Initialize the protocol."""
        self.host = host

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Return True if connected to the device."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the device."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the device."""

    @abstractmethod
    async def send_command(self, command: str, timeout: float = 5.0) -> str:
        """Send a command and return the response string."""

    @abstractmethod
    async def get_version(self) -> str | None:
        """Get firmware version string."""

    @abstractmethod
    async def get_device_info(self) -> DeviceInfo:
        """Get full device information."""

    @abstractmethod
    async def get_connection_count(self) -> int:
        """Get number of active connections."""

    async def validate(self) -> ConnectionInfo | None:
        """Validate connection and return device info, or None on failure."""
        try:
            await self.connect()
            info = await self.get_device_info()
            version = await self.get_version()
            # Prefer M99/main_firmware from M2003 — get_version() on the WS
            # protocol hits /system?action=version which returns the ESP32
            # firmware (same as M2099), not the main MCU firmware.
            firmware = info.main_firmware or version or ""
            return ConnectionInfo(
                host=self.host,
                name=info.device_name or DEFAULT_DEVICE_NAME,
                serial_number=info.serial_number,
                firmware_version=firmware,
                laser_power_watts=info.laser_power_watts,
                device_info=info,
            )
        except Exception as err:
            _LOGGER.debug("Validation failed for %s: %s", self.host, err)
            return None
        finally:
            await self.disconnect()


# --- Connection Validation (auto-detects protocol) ---


async def validate_connection(host: str) -> ConnectionInfo | None:
    """Validate connection to an xTool device, auto-detecting the protocol.

    Tries protocols in order: WS M-code (S1), REST API, HTTP M-code (D-series).
    """
    from .http_mcode_protocol import HttpMcodeProtocol
    from .rest_protocol import RestProtocol
    from .ws_protocol import WsMcodeProtocol

    # Try WS M-code first (S1) — check HTTP version endpoint + WS
    ws = WsMcodeProtocol(host)
    result = await ws.validate()
    if result:
        result.protocol_type = ProtocolType.WS_MCODE
        return result

    # Try REST API (F1/P2/M1 etc) — check /device/machineInfo
    rest = RestProtocol(host)
    result = await rest.validate()
    if result:
        result.protocol_type = ProtocolType.REST
        return result

    # Try HTTP M-code (D-series) — send M99 via HTTP POST
    http_mcode = HttpMcodeProtocol(host)
    result = await http_mcode.validate()
    if result:
        result.protocol_type = ProtocolType.HTTP_MCODE
        return result

    return None
