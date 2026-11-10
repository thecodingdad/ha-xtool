"""Validated projections of observed X1 read-only responses."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..base import XtoolDeviceState


class X1ProtocolError(ConnectionError):
    """Malformed, refused, or incomplete X1 protocol response."""


def content(value: object) -> object:
    if isinstance(value, dict) and value.get("type") == "data":
        if "content" not in value:
            raise X1ProtocolError("X1 data envelope lacks content")
        return value["content"]
    return value


@dataclass(frozen=True)
class X1CameraInfo:
    name: str
    format: str
    width: int
    height: int


def parse_cameras(value: object) -> dict[str, X1CameraInfo]:
    value = content(value)
    if not isinstance(value, list):
        raise X1ProtocolError("Invalid X1 camera list")
    cameras = {}
    ids = set()
    for row in value:
        if (
            not isinstance(row, dict)
            or type(row.get("id")) is not int
            or row["id"] < 0
            or row["id"] in ids
            or not isinstance(row.get("name"), str)
            or not row["name"]
            or row["name"] in cameras
            or not isinstance(row.get("format"), str)
            or any(
                type(row.get(key)) is not int or row[key] <= 0
                for key in ("width", "height", "fps")
            )
            or not isinstance(row.get("version"), str)
            or row["width"] * row["height"] > 20_000_000
        ):
            raise X1ProtocolError("Invalid X1 camera entry")
        ids.add(row["id"])
        cameras[row["name"]] = X1CameraInfo(
            row["name"], row["format"], row["width"], row["height"]
        )
    return cameras


@dataclass(frozen=True)
class X1Module:
    online: bool
    common_status: str | None
    app_version: str | None
    bootloader: bool


def parse_modules(value: object) -> dict[int, X1Module]:
    value = content(value)
    if not isinstance(value, list):
        raise X1ProtocolError("Invalid X1 accessory list")
    modules = {}
    for row in value:
        if (
            not isinstance(row, dict)
            or type(row.get("dev_id")) is not int
            or row["dev_id"] < 0
            or row["dev_id"] in modules
            or type(row.get("status")) is not bool
            or type(row.get("bootloader_status")) is not bool
            or not isinstance(row.get("common_status"), str)
            or not isinstance(row.get("name"), str)
            or not isinstance(row.get("version"), dict)
            or any(
                not isinstance(row["version"].get(key), str)
                for key in ("app_version", "boot_version", "hard_version")
            )
        ):
            raise X1ProtocolError("Invalid X1 accessory entry")
        modules[row["dev_id"]] = X1Module(
            row["status"],
            row["common_status"] or None,
            row["version"]["app_version"] or None,
            row["bootloader_status"],
        )
    return modules


def parse_report(value: object) -> dict[str, str | None]:
    if not isinstance(value, dict):
        raise X1ProtocolError("Invalid X1 state report")
    result = {}
    for key in ("mode", "moveState", "gohomeState"):
        item = value.get(key)
        if item is not None and (
            not isinstance(item, str) or not item or len(item) > 255
        ):
            raise X1ProtocolError("Invalid X1 raw state")
        result[key] = item
    return result


@dataclass(frozen=True)
class X1ConnectedDevice:
    slot: int
    type_prefix: str
    connected: bool


def parse_connected(value: object) -> tuple[X1ConnectedDevice, ...]:
    value = content(value)
    if not isinstance(value, dict) or not isinstance(value.get("result"), str):
        raise X1ProtocolError("Invalid X1 connected inventory")
    result = value["result"]
    if result == "M9098 none":
        return ()
    if not result.startswith("M9098 "):
        raise X1ProtocolError("Invalid X1 connected-list command")
    devices = []
    slots = set()
    addresses = set()
    for row in result[6:].split(";"):
        if not row:
            continue
        fields = row.split(",")
        if (
            len(fields) != 4
            or re.fullmatch(r"[0-9]+", fields[0]) is None
            or re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", fields[1]) is None
            or re.fullmatch(r"[0-9a-fA-F]{2}[A-Za-z0-9._-]*", fields[2]) is None
            or fields[3] not in ("0", "1")
        ):
            raise X1ProtocolError("Invalid X1 connected-list row")
        slot, address = int(fields[0]), fields[1].lower()
        if slot in slots or address in addresses:
            raise X1ProtocolError("Duplicate X1 connected-list row")
        slots.add(slot)
        addresses.add(address)
        devices.append(X1ConnectedDevice(slot, fields[2][:2].upper(), fields[3] == "1"))
    if not devices:
        raise X1ProtocolError("X1 connected list lacks an explicit result")
    return tuple(devices)


@dataclass
class X1ReadOnlyState(XtoolDeviceState):
    raw_states: dict[str, str | None] = field(default_factory=dict)
    cameras: dict[str, X1CameraInfo] = field(default_factory=dict)
    modules: dict[int, X1Module] = field(default_factory=dict)
    bluetooth: tuple[X1ConnectedDevice, ...] | None = None
