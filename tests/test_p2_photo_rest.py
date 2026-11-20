"""After a slow photo the P2 rests before the next one.

Seen 2026-11-16: with a photo every 5 s, photos took 7 to 30 s, some
timed out, and the P2 stopped answering pings while it sent them.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from .features import requires

pytestmark = requires("p2_photo_rest")


class _Session:
    """Answers each photo after ``delay`` seconds, or fails after it."""

    def __init__(self, delay: float, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.urls: list[str] = []

    def get(self, url, timeout=None):
        session = self

        class _Response:
            status = 200

            async def __aenter__(self):
                session.urls.append(url)
                await asyncio.sleep(session.delay)
                if session.fail:
                    raise TimeoutError
                return self

            async def __aexit__(self, *exc):
                return None

            async def read(self):
                return b"jpeg"

        return _Response()


def _protocol(monkeypatch, delay: float, fail: bool = False):
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    proto = P2Protocol("192.0.2.46")
    session = _Session(delay, fail)
    monkeypatch.setattr(proto, "_session", lambda: session)
    return proto, session


async def test_a_fast_photo_needs_no_rest(monkeypatch) -> None:
    proto, session = _protocol(monkeypatch, 0.0)
    assert await proto.camera_snap("overview") == b"jpeg"
    assert await proto.camera_snap("overview") == b"jpeg"
    assert len(session.urls) == 2


async def test_no_new_photo_while_resting_after_a_slow_one(monkeypatch) -> None:
    proto, session = _protocol(monkeypatch, 0.05)
    proto.PHOTO_SLOW_SECONDS = 0.01
    assert await proto.camera_snap("overview") == b"jpeg"
    # Both cameras rest: the P2 sends both cameras' photos
    assert await proto.camera_snap("overview") is None
    assert await proto.camera_snap("closeup") is None
    assert len(session.urls) == 1
    proto._camera_rest_until = 0.0
    assert await proto.camera_snap("closeup") == b"jpeg"


async def test_the_rest_is_as_long_as_the_photo_up_to_a_limit(monkeypatch) -> None:
    proto, _ = _protocol(monkeypatch, 0.05)
    proto.PHOTO_SLOW_SECONDS = 0.01
    proto.PHOTO_REST_MAX = 0.02
    await proto.camera_snap("overview")
    assert 0 < proto._camera_rest_until - time.monotonic() <= 0.02


async def test_a_photo_that_times_out_also_rests(monkeypatch) -> None:
    proto, session = _protocol(monkeypatch, 0.05, fail=True)
    proto.PHOTO_SLOW_SECONDS = 0.01
    with pytest.raises(TimeoutError):
        await proto.camera_snap("overview")
    assert await proto.camera_snap("overview") is None
    assert len(session.urls) == 1


def test_limits() -> None:
    from custom_components.xtool.protocols.rest.p2 import P2Protocol

    assert P2Protocol.PHOTO_SLOW_SECONDS >= 3
    assert P2Protocol.PHOTO_REST_MAX <= 60
