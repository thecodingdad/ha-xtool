"""Owner-issued X1 grants, stored privately outside config-entry options."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from ipaddress import IPv4Address
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store

GRANT_CONFIGURED = "x1_grant_configured"
MAX_GRANT_JSON = 16384
_FIELD_LIMITS = {
    "host": 15,
    "serial": 256,
    "client_id": 36,
    "consumer": 128,
    "leaf_sha256": 64,
    "user_id": 64,
    "timezone": 128,
    "token": 8192,
}


class X1AuthError(ConnectionError):
    """A bound grant was missing, rejected, or did not match the device."""


@dataclass(frozen=True, repr=False)
class X1Grant:
    host: str
    serial: str
    client_id: str
    consumer: str
    leaf_sha256: str
    user_id: str
    timezone: str
    token: str

    @classmethod
    def parse(cls, value: object, *, host: str, serial: str) -> X1Grant:
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise X1AuthError("Invalid X1 grant fields")
        if any(
            not isinstance(item, str)
            or not item
            or item != item.strip()
            or len(item) > _FIELD_LIMITS[key]
            for key, item in value.items()
        ):
            raise X1AuthError("Invalid X1 grant values")
        try:
            address = IPv4Address(value["host"])
            if str(UUID(value["client_id"])) != value["client_id"]:
                raise X1AuthError("X1 client UUID must be canonical")
            ZoneInfo(value["timezone"])
        except (ValueError, ZoneInfoNotFoundError):
            raise X1AuthError("Invalid X1 grant binding") from None
        if (
            str(address) != host
            or value["serial"] != serial
            or not serial
            or address.is_multicast
            or address.is_unspecified
            or address.is_loopback
            or int(address) == 0xFFFFFFFF
            or value["user_id"] != "mk-guest"
            or re.fullmatch(r"[0-9a-fA-F]{64}", value["leaf_sha256"]) is None
        ):
            raise X1AuthError("X1 grant binding mismatch")
        return cls(**value)


def grant_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, str]]:
    from homeassistant.helpers.storage import Store

    return Store(
        hass, 1, f"xtool.x1_grant.{entry_id}", private=True, atomic_writes=True
    )


async def load_grant(
    hass: HomeAssistant, entry_id: str, *, host: str, serial: str
) -> X1Grant:
    return X1Grant.parse(
        await grant_store(hass, entry_id).async_load(), host=host, serial=serial
    )


async def save_grant(hass: HomeAssistant, entry_id: str, grant: X1Grant) -> None:
    await grant_store(hass, entry_id).async_save(asdict(grant))
    stored = await load_grant(hass, entry_id, host=grant.host, serial=grant.serial)
    if stored != grant:
        raise X1AuthError("X1 private grant persistence verification failed")
