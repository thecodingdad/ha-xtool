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


async def _probe(
    target: str, timeout: float, broadcast: bool
) -> list[DiscoveredDevice]:
    """Send UDP discovery packet to a target IP (or broadcast); collect replies."""
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
                    _LOGGER.debug(
                        "Discovered xTool device: %s at %s",
                        response.get("name"), response["ip"],
                    )
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

    try:
        transport, _ = await loop.create_datagram_endpoint(
            _Protocol,
            local_addr=("0.0.0.0", 0),
            allow_broadcast=broadcast,
        )
        transport.sendto(payload, (target, DISCOVERY_PORT))
        await asyncio.sleep(timeout)
    except OSError as err:
        _LOGGER.debug("UDP probe to %s failed: %s", target, err)
    finally:
        if transport:
            transport.close()

    return devices


async def discover_devices(
    timeout: float = DISCOVERY_TIMEOUT,
) -> list[DiscoveredDevice]:
    """Broadcast the discovery packet and collect every responder."""
    return await _probe("255.255.255.255", timeout, broadcast=True)


async def identify_host(
    host: str, timeout: float = 3.0
) -> DiscoveredDevice | None:
    """Send the discovery packet unicast to a single host.

    Returns the device's reported name + version, or None if no reply arrives
    within the timeout.
    """
    results = await _probe(host, timeout, broadcast=False)
    for device in results:
        if device.ip == host:
            return device
    return results[0] if results else None
