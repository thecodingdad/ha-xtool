"""UDP discovery for xTool laser devices on the local network.

Two wire formats coexist on the LAN:

- **V1 (legacy)** — plain JSON `{requestId}` to `255.255.255.255:20000`.
  S1, D-series and any V1-firmware F1/M1/P2 device replies.
- **V2 (encrypted multicast)** — AES-256-CBC encrypted `deviceFind`
  packet to four multicast groups (or two unicast ports for manual
  IP). F1 ≥40.51, F2 family, M1 Ultra, P2S, P3, MetalFab, Apparel
  printer, GS003, GS005 on V2 firmware reply only here.

xTool Studio's `discover-worker.d0392b78.cjs` runs both legs in
parallel — this module mirrors that. See `docs/PROTOCOL.md`,
section "Discovery", for the full wire reference.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import socket
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import DEFAULT_DEVICE_NAME

_LOGGER = logging.getLogger(__name__)

DISCOVERY_TIMEOUT = 5.0

# --- V1 (legacy plain UDP) ----------------------------------------------

V1_UDP_PORT = 20000

# --- V2 (encrypted multicast) -------------------------------------------

V2_MULTICAST_TARGETS: tuple[tuple[str, int, int], ...] = (
    ("224.0.0.251", 5353, 1),    # link-local
    ("224.0.0.252", 5354, 1),    # link-local
    ("239.0.1.251", 25353, 4),   # private
    ("239.0.1.252", 25354, 4),   # private
)
V2_UNICAST_PORTS: tuple[int, ...] = (25353, 25454)
V2_COMMON_KEY = b"makeblocsdbfjssjkkejqbcsdjfbqlla"  # 32 bytes — AES-256
V2_CLIENT_TYPE = "atomnClient"
V2_PROTOCOL_VERSION_FIELD = "1.0"


@dataclass
class DiscoveredDevice:
    """A discovered xTool device on the network."""

    ip: str
    name: str
    version: str
    # ``"V1"`` or ``"V2"`` — set by the probe that produced the result.
    # ``validate_connection`` short-circuits the port-28900 probe when
    # this is already ``"V2"``.
    protocol_version: str = "V1"
    # Populated by V2 (`deviceSn`); empty for V1 since the legacy reply
    # carries no serial.
    serial_number: str = ""


# --- V2 crypto helpers --------------------------------------------------


def _v2_encrypt(plaintext: bytes) -> bytes:
    """AES-256-CBC + PKCS7. Random 16-byte IV prepended to ciphertext."""
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(V2_COMMON_KEY), modes.CBC(iv))
    enc = cipher.encryptor()
    ciphertext = enc.update(padded) + enc.finalize()
    return iv + ciphertext


def _v2_decrypt(blob: bytes) -> dict | None:
    """Inverse of `_v2_encrypt`. Returns parsed JSON or None on any error."""
    if len(blob) < 32:
        return None
    iv, ciphertext = blob[:16], blob[16:]
    try:
        cipher = Cipher(algorithms.AES(V2_COMMON_KEY), modes.CBC(iv))
        dec = cipher.decryptor()
        padded = dec.update(ciphertext) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        return json.loads(plaintext.decode("utf-8"))
    except Exception:
        return None


def _v2_build_request(request_id: int) -> bytes:
    """Build + encrypt the `deviceFind` handshake payload."""
    payload = {
        "type": "deviceFind",
        "method": "request",
        "data": {
            "version": V2_PROTOCOL_VERSION_FIELD,
            "clientType": V2_CLIENT_TYPE,
            "requestId": request_id,
            "key": V2_COMMON_KEY.decode("ascii"),
        },
    }
    return _v2_encrypt(json.dumps(payload).encode("utf-8"))


def _v2_parse_response(
    payload: dict, expected_request_id: int, source_ip: str,
) -> DiscoveredDevice | None:
    """Validate a decrypted V2 frame and convert to ``DiscoveredDevice``."""
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "deviceFind" or payload.get("method") != "response":
        return None
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return None
    if data.get("requestId") != expected_request_id:
        return None
    return DiscoveredDevice(
        ip=data.get("deviceIp") or data.get("ip") or source_ip,
        name=data.get("deviceName") or DEFAULT_DEVICE_NAME,
        version=data.get("firmwareVersion") or data.get("version") or "",
        protocol_version="V2",
        serial_number=data.get("deviceSn") or "",
    )


# --- V1 probe (unchanged behaviour) -------------------------------------


async def _probe_v1(
    target: str, timeout: float, broadcast: bool,
) -> list[DiscoveredDevice]:
    """Send legacy plain-JSON UDP probe; collect replies until timeout."""
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
                            protocol_version="V1",
                        )
                    )
                    _LOGGER.debug(
                        "V1 discovered %s at %s",
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
        transport.sendto(payload, (target, V1_UDP_PORT))
        await asyncio.sleep(timeout)
    except OSError as err:
        _LOGGER.debug("V1 UDP probe to %s failed: %s", target, err)
    finally:
        if transport:
            transport.close()

    return devices


# --- V2 probe (encrypted multicast/unicast) -----------------------------


def _make_v2_socket() -> socket.socket | None:
    """Bind a UDP socket and join all four V2 multicast groups."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Bind on a random port — devices reply via unicast to whatever
        # source-port we used, plus possibly via multicast (received via
        # the group memberships joined below).
        sock.bind(("0.0.0.0", 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        except OSError:
            pass
        for group, _port, _ttl in V2_MULTICAST_TARGETS:
            try:
                mreq = struct.pack(
                    "4sl", socket.inet_aton(group), socket.INADDR_ANY
                )
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq,
                )
            except OSError as err:
                _LOGGER.debug("V2 multicast join %s failed: %s", group, err)
        sock.setblocking(False)
        return sock
    except OSError as err:
        _LOGGER.debug("V2 UDP socket setup failed: %s", err)
        return None


async def _probe_v2(
    target: str, timeout: float, broadcast: bool,
) -> list[DiscoveredDevice]:
    """Encrypted V2 discovery — multicast (broadcast) or unicast.

    Broadcast mode: send to all four multicast targets. Unicast mode:
    send to ``target:25353`` and ``target:25454``. RX socket joins the
    multicast groups so replies on either path are picked up.
    """
    devices: list[DiscoveredDevice] = []
    seen: set[str] = set()
    request_id = random.randint(0, 0xFFFFFFFF)
    blob = _v2_build_request(request_id)

    sock = _make_v2_socket()
    if sock is None:
        return devices

    loop = asyncio.get_event_loop()

    def _on_message(data: bytes, addr: tuple[str, int]) -> None:
        decoded = _v2_decrypt(data)
        if decoded is None:
            return
        device = _v2_parse_response(decoded, request_id, addr[0])
        if device is None or device.ip in seen:
            return
        seen.add(device.ip)
        devices.append(device)
        _LOGGER.debug(
            "V2 discovered %s at %s (sn=%s, fw=%s)",
            device.name, device.ip, device.serial_number, device.version,
        )

    class _Protocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            _on_message(data, addr)

    transport = None
    try:
        transport, _ = await loop.create_datagram_endpoint(
            _Protocol, sock=sock,
        )
        if broadcast:
            for group, port, ttl in V2_MULTICAST_TARGETS:
                try:
                    sock.setsockopt(
                        socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl,
                    )
                    transport.sendto(blob, (group, port))
                except OSError as err:
                    _LOGGER.debug(
                        "V2 send to %s:%d failed: %s", group, port, err,
                    )
        else:
            for port in V2_UNICAST_PORTS:
                try:
                    transport.sendto(blob, (target, port))
                except OSError as err:
                    _LOGGER.debug(
                        "V2 send to %s:%d failed: %s", target, port, err,
                    )
        await asyncio.sleep(timeout)
    except OSError as err:
        _LOGGER.debug("V2 probe to %s failed: %s", target, err)
    finally:
        if transport:
            transport.close()

    return devices


# --- public API --------------------------------------------------------


def _dedupe(devices: list[DiscoveredDevice]) -> list[DiscoveredDevice]:
    """V2 wins on IP collision (richer fields)."""
    by_ip: dict[str, DiscoveredDevice] = {}
    for dev in devices:
        existing = by_ip.get(dev.ip)
        if existing is None or (
            dev.protocol_version == "V2" and existing.protocol_version != "V2"
        ):
            by_ip[dev.ip] = dev
    return list(by_ip.values())


async def discover_devices(
    timeout: float = DISCOVERY_TIMEOUT,
) -> list[DiscoveredDevice]:
    """Broadcast V1 + V2 discovery in parallel; collect every responder."""
    v1, v2 = await asyncio.gather(
        _probe_v1("255.255.255.255", timeout, broadcast=True),
        _probe_v2("255.255.255.255", timeout, broadcast=True),
    )
    return _dedupe(v1 + v2)


async def identify_host(
    host: str, timeout: float = 3.0,
) -> DiscoveredDevice | None:
    """Send unicast V1 + V2 probes in parallel to a single host.

    Returns the first valid reply; V2 wins on tie because the richer
    fields populate the config entry directly.
    """
    v1, v2 = await asyncio.gather(
        _probe_v1(host, timeout, broadcast=False),
        _probe_v2(host, timeout, broadcast=False),
    )
    for dev in v2:
        if dev.ip == host:
            return dev
    if v2:
        return v2[0]
    for dev in v1:
        if dev.ip == host:
            return dev
    return v1[0] if v1 else None
