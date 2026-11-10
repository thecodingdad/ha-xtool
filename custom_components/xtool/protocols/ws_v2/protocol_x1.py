"""X1/JS001 identity discovery before an authorized grant is imported."""

from __future__ import annotations

from ipaddress import IPv4Address

from ...discovery import DiscoveredDevice, _probe_v2
from ..base import DeviceInfo, XtoolDeviceState, XtoolProtocol


class X1DiscoveryProtocol(XtoolProtocol):
    """Read only identity; deliberately has no instruction/control transport."""

    def __init__(self, host: str) -> None:
        address = IPv4Address(host)
        if (
            address.is_multicast
            or address.is_unspecified
            or address.is_loopback
            or int(address) == 0xFFFFFFFF
        ):
            raise ValueError("X1 monitoring requires a unicast IPv4 address")
        super().__init__(host)
        self._discovery: DiscoveredDevice | None = None
        self._expected_serial = ""

    @property
    def discovery(self) -> DiscoveredDevice | None:
        return self._discovery

    @property
    def connected(self) -> bool:
        """Last discovery succeeded; this does not mean API authorization."""
        return self._discovery is not None

    def bind_serial(self, serial: str) -> None:
        """Keep a saved entry attached to its device across DHCP changes."""
        if self._expected_serial and serial != self._expected_serial:
            raise ValueError("X1 identity already bound")
        self._expected_serial = serial

    async def refresh(self) -> None:
        self._discovery = None
        devices = await _probe_v2(
            self.host,
            timeout=5.0,
            broadcast=False,
            log_identifiers=False,
        )
        matches = [device for device in devices if device.ip == self.host]
        if len(matches) != 1:
            raise ConnectionError("X1 discovery returned no unique target")
        device = matches[0]
        if (
            device.protocol_version != "V2"
            or device.device_code != "JS001"
            or device.platform_version != "2.0"
            or any(
                not isinstance(value, str) or not value.strip()
                for value in (device.name, device.serial_number, device.version)
            )
        ):
            raise ConnectionError("X1 discovery identity is incomplete or unsupported")
        if self._expected_serial and device.serial_number != self._expected_serial:
            raise ConnectionError("X1 discovery does not match the saved device")
        self._expected_serial = device.serial_number
        self._discovery = device

    async def connect(self) -> None:
        if not self.connected:
            await self.refresh()

    async def disconnect(self) -> None:
        self._discovery = None

    async def get_device_info(self) -> DeviceInfo:
        await self.connect()
        device = self._discovery
        assert device is not None
        return DeviceInfo(
            serial_number=device.serial_number,
            device_name=device.name,
            main_firmware=device.version,
        )

    async def get_version(self) -> str:
        return (await self.get_device_info()).main_firmware

    async def poll_state(self, state: XtoolDeviceState) -> None:
        await self.refresh()
        info = await self.get_device_info()
        state.available = True
        state.device_name = info.device_name
        state.serial_number = info.serial_number
        state.firmware_version = info.main_firmware
        state.status = None
