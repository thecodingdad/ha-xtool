"""P2 photos never queue up, and a live view asks for at most one frame per second.

Seen 2026-11-15: while the Overview camera's live view ran, the P2 slowed
down until a ping took 300 ms and a poll 27 s, and Home Assistant with it.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from .features import requires

pytestmark = requires("p2_camera_queue")


class _Session:
    """Answers each photo after a short wait, counting overlapping photos."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.active = 0
        self.most_active = 0

    def get(self, url, timeout=None):
        session = self

        class _Response:
            status = 200

            async def __aenter__(self):
                session.urls.append(url)
                session.active += 1
                session.most_active = max(session.most_active, session.active)
                return self

            async def __aexit__(self, *exc):
                session.active -= 1

            async def read(self):
                await asyncio.sleep(0.05)
                return url.encode()

        return _Response()


def _protocol(monkeypatch):
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("192.0.2.46")
    session = _Session()
    monkeypatch.setattr(proto, "_session", lambda: session)
    return proto, session


async def test_no_photo_while_the_p2_does_not_answer(monkeypatch) -> None:
    proto, session = _protocol(monkeypatch)
    proto._offline_until = time.monotonic() + 30
    assert await proto.camera_snap("overview") is None
    assert session.urls == []


async def test_requests_share_the_photo_on_its_way(monkeypatch) -> None:
    proto, session = _protocol(monkeypatch)
    results = await asyncio.gather(
        *(proto.camera_snap("overview") for _ in range(20)), proto.camera_snap("closeup"),
    )
    # One photo per camera, and never two at once
    assert sorted(session.urls) == sorted({url.decode() for url in results})
    assert len(session.urls) == 2
    assert session.most_active == 1
    # The next request takes a new photo
    await proto.camera_snap("overview")
    assert len(session.urls) == 3


async def test_a_viewer_that_leaves_does_not_cancel_the_photo(monkeypatch) -> None:
    proto, session = _protocol(monkeypatch)
    first = asyncio.ensure_future(proto.camera_snap("overview"))
    second = asyncio.ensure_future(proto.camera_snap("overview"))
    await asyncio.sleep(0)
    first.cancel()
    assert (await second).endswith(b"stream=0")
    assert len(session.urls) == 1


@requires("p2_head_read_per_photo")
async def test_a_cached_picture_does_not_read_the_head(hass, config_entry) -> None:
    """Seen 2026-11-15: two live views read the head about 10 times a second."""
    from unittest.mock import AsyncMock

    from homeassistant.util import dt as dt_util

    from custom_components.xtool.protocols.base import XtoolDeviceState
    from custom_components.xtool.protocols.rest import XTOOL_P2
    from custom_components.xtool.protocols.rest import p2_entities

    from .conftest import build_coordinator

    coord = build_coordinator(hass, XTOOL_P2)
    coord.config_entry = config_entry
    coord.data = XtoolDeviceState()
    p2_entities._p2_view(coord)[p2_entities._overlay_key("overview", "laser_target")] = True
    coord.protocol.read_head_position = AsyncMock(return_value=(1.0, 2.0))
    camera = next(c for c in coord.build_cameras() if c._camera_name == "overview")
    camera._last_snapshot = b"cached"
    camera._last_snapshot_time = dt_util.utcnow()
    assert await camera._image(p2_entities.MIN_SNAPSHOT_INTERVAL) == b"cached"
    coord.protocol.read_head_position.assert_not_awaited()


def test_p2_live_view_is_one_frame_per_second() -> None:
    from custom_components.xtool.protocols.rest.p2_entities import P2Camera
    from custom_components.xtool.protocols.ws_v2.entities import (
        LIVE_FRAME_INTERVAL,
        _WSV2Camera,
    )

    assert P2Camera._live_frame_interval == pytest.approx(1.0)
    # Other lasers keep their pace
    assert _WSV2Camera._live_frame_interval == LIVE_FRAME_INTERVAL
