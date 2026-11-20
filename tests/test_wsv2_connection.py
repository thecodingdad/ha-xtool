"""WS-V2 Connection sensor: how Home Assistant reaches the laser.

The xTool card shows a connection badge from a sensor with the
translation key "connection". The F2 Ultra UV lists its interfaces with
their addresses (/v1/wifi/interfaces); the one with the address Home
Assistant connects to tells Ethernet or Wi-Fi. Nothing is guessed from
the model.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from .conftest import build_coordinator

HOST = "192.0.2.10"
INTERFACES = [
    {"iface": "lo", "ip": "127.0.0.1"},
    {"iface": "usb0", "ip": "192.168.7.1"},
    {"iface": "usb1", "ip": "192.168.8.1"},
    {"iface": "wlan0", "ip": HOST},
]


def _model(model_id: str = "F2UltraUV"):
    from custom_components.xtool.protocols import DEVICE_MODELS

    return next(
        m for m in DEVICE_MODELS.values()
        if m.model_id == model_id and m.protocol_version == "V2"
    )


def _protocol(reply):
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol(HOST)
    proto._model = _model()
    proto._pending_model = proto._model
    proto._connected = True
    proto.connect = AsyncMock()

    async def request(url, method="GET", params=None, data=None, **kwargs):
        if url == "/v1/wifi/interfaces" and reply is not None:
            return reply
        raise RuntimeError(f"V2 {method} {url} returned code 1: failed")

    proto.request = request
    return proto


@pytest.mark.parametrize(
    ("iface", "expected"),
    [("wlan0", "wifi"), ("eth0", "ethernet"), ("usb0", None), ("lo", None), ("", None)],
)
def test_interface_names(iface: str, expected: str | None) -> None:
    from custom_components.xtool.protocols.ws_v2.protocol import connection_of_interface

    assert connection_of_interface(iface) == expected


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (INTERFACES, "wifi"),
        ([{"iface": "eth0", "ip": HOST}, {"iface": "wlan0", "ip": "192.0.2.11"}], "ethernet"),
        ({"interfaces": INTERFACES}, "wifi"),
        # No interface has the address in use (for example a host name)
        ([{"iface": "wlan0", "ip": "192.0.2.99"}], None),
        # The address in use is the USB network link: no state
        ([{"iface": "usb0", "ip": HOST}], None),
        # The read fails
        (None, None),
    ],
)
async def test_connection_from_the_interface_with_the_address_in_use(reply, expected) -> None:
    proto = _protocol(reply)
    await proto._read_connection()
    assert proto._latest.get("connection") == expected


def _connection_sensors(hass: HomeAssistant, model):
    coordinator = build_coordinator(hass, model)
    return coordinator, [
        e for e in coordinator.build_sensors()
        if getattr(e, "translation_key", None) == "connection"
    ]


async def test_f2_ultra_uv_has_the_sensor(hass: HomeAssistant, config_entry) -> None:
    from homeassistant.components.sensor import SensorDeviceClass
    from homeassistant.const import EntityCategory

    from custom_components.xtool.protocols.base import XtoolDeviceState

    coordinator, sensors = _connection_sensors(hass, _model())
    assert len(sensors) == 1
    sensor = sensors[0]
    assert sensor.unique_id.endswith("connection")
    assert sensor.device_class == SensorDeviceClass.ENUM
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.options == ["ethernet", "wifi"]
    coordinator.data = XtoolDeviceState(available=True, connection="wifi")
    assert sensor.available
    assert sensor.native_value == "wifi"
    # Unavailable while the laser cannot be reached, like the other entities
    coordinator.data = XtoolDeviceState(available=False, connection="wifi")
    assert not sensor.available


async def test_only_lasers_that_report_their_interfaces(hass: HomeAssistant, config_entry) -> None:
    from custom_components.xtool.protocols import DEVICE_MODELS

    for model in DEVICE_MODELS.values():
        if model.coordinator_class is not _model().coordinator_class:
            continue
        _, sensors = _connection_sensors(hass, model)
        assert len(sensors) == (1 if getattr(model, "has_laser_info", False) else 0), model.model_id


def test_translations() -> None:
    import json

    from .common import COMPONENT

    for path in ("translations/en.json",):
        data = json.loads((COMPONENT / path).read_text(encoding="utf-8"))
        assert data["entity"]["sensor"]["connection"]["state"] == {"ethernet": "Ethernet", "wifi": "Wi-Fi"}


def _polling_protocol(answers):
    """A protocol whose interface read gives each of ``answers`` in turn."""
    from custom_components.xtool.protocols.base import XtoolDeviceState  # noqa: F401
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol(HOST)
    proto._model = _model()
    proto._pending_model = proto._model
    proto._connected = True
    proto.connect = AsyncMock()
    proto._poll_laser_info = AsyncMock()
    proto.urls = []
    replies = list(answers)

    async def request(url, method="GET", params=None, data=None, **kwargs):
        proto.urls.append(url)
        if url == "/v1/wifi/interfaces":
            reply = replies.pop(0) if replies else INTERFACES
            if reply is None:
                raise TimeoutError
            return reply
        return {}

    proto.request = request
    return proto


async def test_read_first_in_the_poll_once_per_connection() -> None:
    """The first poll after the laser powers on carries the connection."""
    from custom_components.xtool.protocols.base import XtoolDeviceState

    proto = _polling_protocol([INTERFACES])
    state = XtoolDeviceState()
    await proto.poll_state(state)
    assert state.connection == "wifi"
    # Read right after the runtime status, before the peripheral reads
    assert proto.urls.index("/v1/wifi/interfaces") == 1
    proto.urls.clear()
    await proto.poll_state(XtoolDeviceState())
    assert "/v1/wifi/interfaces" not in proto.urls


async def test_read_again_on_the_next_poll_while_the_laser_boots() -> None:
    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.ws_v2.protocol import LASER_INFO_TRIES

    proto = _polling_protocol([None, INTERFACES])
    state = XtoolDeviceState()
    await proto.poll_state(state)
    assert state.connection is None
    await proto.poll_state(state)
    assert state.connection == "wifi"
    # A laser that never answers is asked only a few times per connection
    proto = _polling_protocol([None] * 10)
    for _ in range(LASER_INFO_TRIES + 2):
        await proto.poll_state(XtoolDeviceState())
    assert proto.urls.count("/v1/wifi/interfaces") == LASER_INFO_TRIES


async def test_read_again_after_a_reconnect() -> None:
    from custom_components.xtool.protocols.base import XtoolDeviceState

    proto = _polling_protocol([INTERFACES, INTERFACES])
    await proto.poll_state(XtoolDeviceState())
    proto._connection_known = False  # as each new connection sets it
    proto._connection_tries = 0
    proto.urls.clear()
    await proto.poll_state(XtoolDeviceState())
    assert proto.urls.count("/v1/wifi/interfaces") == 1


async def test_sent_to_home_assistant_as_soon_as_it_is_read() -> None:
    """Home Assistant gets the Connection without waiting for the rest of the poll."""
    from unittest.mock import MagicMock

    proto = _polling_protocol([None, INTERFACES])
    proto._push_notify = MagicMock()
    await proto._poll_connection()
    proto._push_notify.assert_not_called()
    await proto._poll_connection()
    proto._push_notify.assert_called_once()
    assert proto._latest["connection"] == "wifi"
