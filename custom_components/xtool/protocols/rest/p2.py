"""xTool P2: the REST API on port 8080 plus the push channel xTool Studio uses.

Studio's P2 extension (``exts.zip`` ``P2/index.js``) keeps a WebSocket open
to ``ws://<laser>:8081`` and sends the text ``ping`` every 3 seconds. The
laser answers with a 10-byte binary keepalive every 2 seconds and pushes
every change as a binary frame: a 10-byte header (bytes 2-5: the length of
the JSON that follows, little-endian) and then JSON such as::

    {"module": "STATUS_CONTROLLER", "type": "MODE_CHANGE",
     "info": {"mode": "Work", "subMode": "working", "taskId": "..."},
     "level": "notice"}
    {"module": "GAP", "type": "OPEN", "info": "", "level": "notice"}

On connect the laser pushes its current mode at once. When neither a push
nor a keepalive arrives for 11 seconds (Studio's heartbeat timeout), the
laser is gone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from ...const import XtoolStatus
from ..base import XtoolDeviceState
from .protocol import REST_HTTP_PORT, RestProtocol, _map_rest_mode

_LOGGER = logging.getLogger(__name__)

P2_PUSH_PORT = 8081
# Studio: heartbeatInterval 3000 ms with pingCmd "ping", heartbeatTimeout 11000 ms
PING_SECONDS = 3.0
PUSH_TIMEOUT_SECONDS = 11.0
CONNECT_TIMEOUT_SECONDS = 5.0
FRAME_HEADER_BYTES = 10

# Studio's getCurrentStatus: "<mode>.<subMode>" -> status. "Work" carries the
# job's phase in its sub-mode.
_WORK_SUB_MODES: dict[str, XtoolStatus] = {
    "WORKREADY": XtoolStatus.PROCESSING_READY,
    "WORKING": XtoolStatus.PROCESSING,
    "WORKPAUSE": XtoolStatus.PAUSED,
}


def map_p2_mode(mode: Any, sub_mode: Any = "") -> XtoolStatus | None:
    """The status for a P2 mode, as Studio reads it."""
    mode_s = str(mode or "").upper()
    sub_s = str(sub_mode or "").upper()
    if mode_s == "WORK":
        status = _WORK_SUB_MODES.get(sub_s)
        if status is None:
            _LOGGER.debug("P2 job sub-mode %r unknown; showing Processing", sub_mode)
            return XtoolStatus.PROCESSING
        return status
    status = _map_rest_mode(mode_s)
    if status is None and mode_s:
        _LOGGER.debug("P2 mode %r (sub-mode %r) unknown, please report", mode, sub_mode)
    return status


def decode_push_frame(data: bytes | str) -> dict[str, Any] | None:
    """The JSON of one push frame, or None for a keepalive or a frame we cannot read."""
    if isinstance(data, str):
        text = data
    else:
        if len(data) < FRAME_HEADER_BYTES:
            return None
        length = int.from_bytes(data[2:6], "little")
        body = data[FRAME_HEADER_BYTES:FRAME_HEADER_BYTES + length] if length else b""
        if not body:
            return None
        text = body.decode("utf-8", "replace")
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        _LOGGER.debug("P2 push not JSON: %r", text[:200])
        return None
    return obj if isinstance(obj, dict) else None


class P2Protocol(RestProtocol):
    """The P2's REST API, with Studio's push channel for live changes."""

    # /peripheral/gap reports "off" while the lid is closed (Studio: isCover)
    GAP_OFF_MEANS_OPEN = False

    def __init__(self, host: str, port: int = REST_HTTP_PORT) -> None:
        super().__init__(host, port)
        self._push_session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._last_frame = 0.0
        # Field -> (value, time it was pushed); applied over each poll
        self._latest: dict[str, tuple[Any, float]] = {}
        # (kind, event type, attributes) for the coordinator to send as events
        self._push_events: list[tuple[str, str, dict[str, Any]]] = []
        self._push_notify: Callable[[], None] | None = None

    # --- Connection -----------------------------------------------------

    @property
    def connected(self) -> bool:
        """True while the push channel is open and the laser is heard from."""
        return (
            self._ws is not None
            and not self._ws.closed
            and time.monotonic() - self._last_frame < PUSH_TIMEOUT_SECONDS
        )

    async def connect(self) -> None:
        """Open the push channel; raises when the laser does not answer."""
        if self.connected:
            return
        await self._close_push()
        self._push_session = aiohttp.ClientSession()
        url = f"ws://{self.host}:{P2_PUSH_PORT}/"
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                self._ws = await self._push_session.ws_connect(url, autoping=True)
        except Exception:
            await self._close_push()
            raise
        self._last_frame = time.monotonic()
        _LOGGER.debug("P2 push channel open: %s", url)
        self._reader = asyncio.create_task(self._read_pushes(self._ws), name=f"xtool_p2_push_{self.host}")

    async def disconnect(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None and reader is not asyncio.current_task() and not reader.done():
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass
        await self._close_push()

    async def _close_push(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass
        session, self._push_session = self._push_session, None
        if session is not None and not session.closed:
            await session.close()

    async def _read_pushes(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Read pushes until the channel closes or the laser goes quiet."""
        next_ping = 0.0
        try:
            while not ws.closed:
                now = time.monotonic()
                if now >= next_ping:
                    await ws.send_str("ping")
                    next_ping = now + PING_SECONDS
                try:
                    msg = await ws.receive(timeout=max(0.1, next_ping - time.monotonic()))
                except asyncio.TimeoutError:
                    if time.monotonic() - self._last_frame >= PUSH_TIMEOUT_SECONDS:
                        _LOGGER.debug("P2 push channel silent for %.0f s", PUSH_TIMEOUT_SECONDS)
                        break
                    continue
                if msg.type in (aiohttp.WSMsgType.BINARY, aiohttp.WSMsgType.TEXT):
                    self._last_frame = time.monotonic()
                    obj = decode_push_frame(msg.data)
                    if obj is not None:
                        self.handle_push(obj)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("P2 push channel failed: %s", err)
        _LOGGER.debug("P2 push channel closed")
        if self._ws is ws:
            self._last_frame = 0.0
            try:
                await ws.close()
            except Exception:
                pass
        self._notify()

    def _notify(self) -> None:
        if self._push_notify is not None:
            self._push_notify()

    # --- Pushes ---------------------------------------------------------

    def _set(self, field: str, value: Any) -> None:
        self._latest[field] = (value, time.monotonic())

    def handle_push(self, obj: dict[str, Any]) -> None:
        """Record what one push says and tell the coordinator."""
        _LOGGER.debug("P2 push: %s", obj)
        module = str(obj.get("module") or "")
        kind = str(obj.get("type") or "")
        info = obj.get("info")
        if module == "STATUS_CONTROLLER":
            if kind == "MODE_CHANGE" and isinstance(info, dict):
                status = map_p2_mode(info.get("mode"), info.get("subMode"))
                if status is not None:
                    self._set("status", status)
                self._set("working_mode", str(info.get("subMode") or ""))
                if info.get("taskId"):
                    self._set("task_id", str(info["taskId"]))
        elif module == "GAP" and kind in ("OPEN", "CLOSE"):
            self._set("cover_open", kind == "OPEN")
        elif module == "DRAWER" and kind in ("OPEN", "CLOSE"):
            self._set("drawer_open", kind == "OPEN")
        elif module == "BUTTON" and kind:
            from .protocol import _normalise_rest_button_event

            event = _normalise_rest_button_event(kind)
            if event:
                self._push_events.append(("button", event, {"raw_type": kind}))
        elif module == "FIRE_RECOGNITION" and kind == "FIND_EXCEPTION":
            self._push_events.append(("fire_warning", "triggered", {"level": info}))
        self._notify()

    def apply_latest(self, state: XtoolDeviceState, since: float = 0.0) -> None:
        """Copy pushed values newer than ``since`` into ``state``."""
        for field, (value, at) in self._latest.items():
            if at >= since:
                setattr(state, field, value)

    def take_push_events(self) -> list[tuple[str, str, dict[str, Any]]]:
        events, self._push_events = self._push_events, []
        return events

    # --- Polling --------------------------------------------------------

    def _map_running_mode(self, cur_mode: dict[str, Any]) -> XtoolStatus | None:
        return map_p2_mode(cur_mode.get("mode"), cur_mode.get("subMode"))

    async def poll_state(self, state: XtoolDeviceState) -> None:
        """Poll, then fail when the laser did not answer, and keep newer pushes."""
        started = time.monotonic()
        await super().poll_state(state)
        if self._is_offline():
            raise ConnectionError(f"P2 at {self.host} did not answer")
        # A push that arrived while the poll ran is newer than what the poll read
        self.apply_latest(state, since=started)
