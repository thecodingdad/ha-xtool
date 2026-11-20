"""A fake WS-V2 device for protocol tests, without a network.

From the tests Jenna Massardo wrote for PR #62.
"""

from __future__ import annotations

import asyncio
from collections import deque
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp

from custom_components.xtool.protocols.ws_v2 import protocol as ws_v2

PAIRING_TOKEN = "test-permanent-credential"
SESSION_TOKEN = "test-session-credential"


class FakeWebSocket:
    """Answers parity from a queue and every other request from ``responses``."""

    def __init__(self, replies: deque, outgoing: list, ping_received: asyncio.Event) -> None:
        self.closed = False
        self.closed_event = asyncio.Event()
        self.replies = replies
        self.responses: dict[str, dict] = {}
        self.outgoing = outgoing
        self.ping_received = ping_received
        self.incoming: asyncio.Queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.incoming.get()

    async def send_bytes(self, frame: bytes) -> None:
        frames, remainder = ws_v2._decode_frames(frame)
        assert not remainder and len(frames) == 1
        payload = json.loads(frames[0][1])
        self.outgoing.append(payload)
        if payload["url"] == "/v1/user/parity":
            response = self.replies.popleft()
        else:
            response = self.responses.get(payload["url"], {"code": 0, "data": {"ok": True}})
        response = {**response, "type": "response", "transactionId": payload["transactionId"]}
        await self.incoming.put(SimpleNamespace(
            type=aiohttp.WSMsgType.BINARY,
            data=ws_v2._encode_frame(ws_v2._json_compact(response).encode()),
        ))
        if payload["url"] == "/v1/user/ping":
            self.ping_received.set()

    async def close(self) -> None:
        self.closed = True
        self.closed_event.set()


class Device:
    """The fake device: the parity replies, what was sent, the sockets opened."""

    def __init__(self) -> None:
        self.replies: deque = deque([{
            "code": 0,
            "data": {
                "authState": "TOKEN_AUTHORIZED",
                "sessionToken": SESSION_TOKEN,
                "token": "test-returned-permanent-credential",
            },
        }])
        self.outgoing: list[dict] = []
        self.sockets: list[FakeWebSocket] = []
        self.ping_received = asyncio.Event()

    def session(self) -> SimpleNamespace:
        ws = FakeWebSocket(self.replies, self.outgoing, self.ping_received)
        self.sockets.append(ws)
        session = SimpleNamespace(closed=False, ws_connect=AsyncMock(return_value=ws))

        async def close() -> None:
            session.closed = True

        session.close = close
        return session
