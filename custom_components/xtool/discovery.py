"""UDP discovery for xTool laser devices on the local network."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass

from .const import DEFAULT_DEVICE_NAME

_LOGGER = logging.getLogger(__name__)

DISCOVERY_PORT = 20000
DISCOVERY_TIMEOUT = 5.0


@dataclass
class DiscoveredDevice:
    """A discovered xTool device on the network."""

    ip: str
    name: str
    version: str


async def discover_devices(timeout: float = DISCOVERY_TIMEOUT) -> list[DiscoveredDevice]:
    """Send UDP broadcast and collect xTool device responses."""
    devices: list[DiscoveredDevice] = []
    request_id = random.randint(100000, 999999)
    payload = json.dumps({"requestId": request_id}).encode()

    loop = asyncio.get_event_loop()
    transport = None

    class _Protocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            try:
                response = json.loads(data.decode())
                if response.get("requestId") == request_id and "ip" in response:
                    devices.append(
                        DiscoveredDevice(
                            ip=response["ip"],
                            name=response.get("name", DEFAULT_DEVICE_NAME),
                            version=response.get("version", ""),
                        )
                    )
                    _LOGGER.debug("Discovered xTool device: %s at %s", response.get("name"), response["ip"])
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

    try:
        transport, _ = await loop.create_datagram_endpoint(
            _Protocol,
            local_addr=("0.0.0.0", 0),
            allow_broadcast=True,
        )
        transport.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))
        await asyncio.sleep(timeout)
    except OSError as err:
        _LOGGER.debug("UDP discovery failed: %s", err)
    finally:
        if transport:
            transport.close()

    return devices
