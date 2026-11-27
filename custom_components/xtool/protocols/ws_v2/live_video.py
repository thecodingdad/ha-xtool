"""The laser's live video, handed to Home Assistant's stream integration.

The laser sends H.264 (Annex B) on its ``media_stream`` socket.
:class:`LiveStreamSource` serves it unchanged on a TCP port of
127.0.0.1, which is the camera's ``stream_source``: Home Assistant's
stream integration (HLS) and go2rtc (WebRTC) read it there and only
repackage it, nothing is decoded. Every reader gets the same video; it
starts with the first reader and stops after the last.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable

_LOGGER = logging.getLogger(__name__)

# NAL unit types a decoder can start at: an SPS (7) or an IDR slice (5)
_START_NAL_TYPES = frozenset((5, 7))
# Access units a reader may fall behind (a few seconds of video) before
# its connection is closed; it then connects again and starts afresh.
_READER_BACKLOG = 64


class LiveVideoTakenOver(Exception):
    """Another camera of the laser went live: this one's video ended."""


def starts_stream(unit: bytes) -> bool:
    """True when a decoder can start at this access unit (an SPS or IDR in it)."""
    index = unit.find(b"\x00\x00\x01")
    while index != -1 and index + 3 < len(unit):
        if unit[index + 3] & 0x1F in _START_NAL_TYPES:
            return True
        index = unit.find(b"\x00\x00\x01", index + 3)
    return False


class LiveStreamSource:
    """One camera's live video on ``tcp://127.0.0.1:<port>``.

    ``video`` opens the laser's video (an iterator of H.264 access
    units, closed to stop it). A reader that joins while the video
    runs gets it from the next access unit a decoder can start at.
    ``on_readers`` is called whenever the number of readers changes,
    ``on_taken_over`` when another camera took the laser's video over.
    """

    def __init__(
        self,
        video: Callable[[], AsyncIterator[bytes]],
        name: str,
        on_readers: Callable[[], None] | None = None,
        on_taken_over: Callable[[], None] | None = None,
    ) -> None:
        self._video = video
        self._name = name
        self._on_readers = on_readers
        self._on_taken_over = on_taken_over
        self._server: asyncio.Server | None = None
        self._readers: set[asyncio.Queue[bytes | None]] = set()
        self._pump: asyncio.Task[None] | None = None

    @property
    def readers(self) -> int:
        return len(self._readers)

    async def url(self) -> str:
        """The source's URL; its server starts on first use."""
        if self._server is None:
            self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        return f"tcp://127.0.0.1:{port}"

    async def close(self) -> None:
        """Stop the video and the server (the camera is removed)."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for queue in list(self._readers):
            _end(queue)
        await self._stop_pump()
        if server is not None:
            await server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(_READER_BACKLOG)
        self._readers.add(queue)
        self._readers_changed()
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._run(), name=f"xTool live video {self._name}")
        # Readers send nothing: the read ends when they close
        gone = asyncio.ensure_future(reader.read())
        started = False
        try:
            while True:
                unit = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({unit, gone}, return_when=asyncio.FIRST_COMPLETED)
                if unit not in done:
                    unit.cancel()
                    break
                data = unit.result()
                if data is None:
                    break
                if not started:
                    if not starts_stream(data):
                        continue
                    started = True
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            gone.cancel()
            self._readers.discard(queue)
            self._readers_changed()
            if not self._readers:
                await self._stop_pump()
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def _run(self) -> None:
        try:
            async with contextlib.aclosing(self._video()) as units:
                async for data in units:
                    for queue in list(self._readers):
                        if queue.full():
                            _LOGGER.debug("Live video %s: a reader fell behind", self._name)
                            _end(queue)
                        else:
                            queue.put_nowait(data)
        except LiveVideoTakenOver:
            _LOGGER.debug("Live video %s ended: another camera went live", self._name)
            if self._on_taken_over is not None:
                self._on_taken_over()
        except Exception as err:  # noqa: BLE001
            # The laser gone: the readers end and connect again later.
            _LOGGER.debug("Live video %s ended: %s", self._name, err)
        for queue in list(self._readers):
            _end(queue)

    async def _stop_pump(self) -> None:
        pump, self._pump = self._pump, None
        if pump is not None and not pump.done():
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump

    def _readers_changed(self) -> None:
        if self._on_readers is not None:
            self._on_readers()


def _end(queue: asyncio.Queue[bytes | None]) -> None:
    """Tell a reader to finish, ahead of what it has not sent yet."""
    while not queue.empty():
        queue.get_nowait()
    queue.put_nowait(None)
