"""V1 discovery finds a P2 by broadcast.

The P2 (firmware 40.21.x) echoes the requestId of a discovery request only
when it fits in 32 bits; a larger one comes back as 0. A reply whose
requestId differs from the one sent is dropped, so the request must use
an ID the P2 can echo.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


LASER_IP = "192.0.2.50"


def _p2_reply(request: dict) -> dict:
    request_id = request.get("requestId", 0)
    echoed = request_id if 0 <= request_id < 2**32 else 0
    return {"ip": LASER_IP, "name": "P2", "requestId": echoed, "version": "40.21.025.2020.01.ht3"}


@pytest.fixture
def fake_network(monkeypatch):
    from custom_components.xtool import discovery

    sent: list[dict] = []

    class FakeLoop:
        async def create_datagram_endpoint(self, factory, **kwargs):
            protocol = factory()
            transport = MagicMock()
            transport.get_extra_info.side_effect = lambda key: ("0.0.0.0", 40000) if key == "sockname" else MagicMock()

            def sendto(data, addr):
                request = json.loads(data)
                sent.append(request)
                protocol.datagram_received(json.dumps(_p2_reply(request)).encode(), (LASER_IP, 20000))

            transport.sendto.side_effect = sendto
            return transport, protocol

    monkeypatch.setattr(discovery.asyncio, "get_event_loop", lambda: FakeLoop())
    monkeypatch.setattr(discovery, "_local_ip_for", lambda target: "192.0.2.10")
    return sent


@pytest.mark.parametrize(("target", "broadcast", "multicast"), [
    ("255.255.255.255", True, False),
    ("224.0.1.77", False, True),
])
async def test_a_p2_answers_broadcast_and_multicast(fake_network, target, broadcast, multicast) -> None:
    from custom_components.xtool.discovery import _probe_v1

    found = await _probe_v1(target, 0.01, broadcast=broadcast, multicast=multicast)
    assert [(d.ip, d.name) for d in found] == [(LASER_IP, "P2")]
    assert 0 < fake_network[0]["requestId"] < 2**31


async def test_each_request_has_its_own_id(fake_network) -> None:
    from custom_components.xtool.discovery import _probe_v1

    for _ in range(5):
        await _probe_v1("255.255.255.255", 0.01, broadcast=True)
    assert len({r["requestId"] for r in fake_network}) > 1
