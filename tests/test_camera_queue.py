"""Camera photo requests never pile up.

While the laser was off, requests queued behind the camera lock faster
than they failed, and once it was back each new picture waited for every
old request (about 20 minutes). Now no photo is asked for while the laser
is not connected, and a request for a camera whose photo is already
coming gets that photo.
"""

from __future__ import annotations

import asyncio

from .features import requires

pytestmark = requires("camera_no_queue")


def _protocol():
    from custom_components.xtool.protocols.ws_v2.protocol import WSV2Protocol

    proto = WSV2Protocol("192.0.2.10")
    proto._connected = True
    return proto


async def test_no_photo_while_not_connected() -> None:
    proto = _protocol()
    proto._connected = False
    calls = 0

    async def snap(name: str) -> bytes:
        nonlocal calls
        calls += 1
        return b"x"

    proto._camera_snap = snap
    assert await proto.camera_snap("main") is None
    assert calls == 0


async def test_requests_for_one_camera_share_its_photo() -> None:
    proto = _protocol()
    calls = []

    async def snap(name: str) -> bytes:
        calls.append(name)
        await asyncio.sleep(0.02)
        return name.encode() + str(len(calls)).encode()

    proto._camera_snap = snap
    results = await asyncio.gather(*(proto.camera_snap("main") for _ in range(20)), proto.camera_snap("deep"))
    assert calls == ["main", "deep"]
    assert results[:20] == [b"main1"] * 20 and results[20] == b"deep2"
    # Once that photo has come, the next request takes a new one
    assert await proto.camera_snap("main") == b"main3"


async def test_a_viewer_that_goes_away_does_not_cancel_the_shared_photo() -> None:
    proto = _protocol()

    async def snap(name: str) -> bytes:
        await asyncio.sleep(0.02)
        return b"photo"

    proto._camera_snap = snap
    first = asyncio.ensure_future(proto.camera_snap("main"))
    second = asyncio.ensure_future(proto.camera_snap("main"))
    await asyncio.sleep(0)
    first.cancel()
    assert await second == b"photo"
