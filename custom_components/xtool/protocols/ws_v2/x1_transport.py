"""Pinned X1 instruction transport. No guest fallback or control interface."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import time
from collections import deque
from typing import Any

import aiohttp

from ..base import DeviceInfo, XtoolProtocol
from .x1_auth import X1AuthError, X1Grant
from .x1_codec import MAX_FRAME as _MAX_FRAME
from .x1_codec import decode_frames, encode_frame
from .x1_data import (
    X1CameraInfo,
    X1ProtocolError,
    X1ReadOnlyState,
    content,
    parse_cameras,
    parse_connected,
    parse_modules,
    parse_report,
)

_TIMEOUT = 10


async def _refuse_redirect(*_: object) -> None:
    raise X1ProtocolError("X1 redirects are forbidden")


def _verify_jpeg(image: bytes, camera: X1CameraInfo) -> None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(image)) as parsed:
            if parsed.format != "JPEG" or parsed.size != (camera.width, camera.height):
                raise X1ProtocolError("X1 snapshot format or dimensions mismatch")
            parsed.verify()
        with Image.open(io.BytesIO(image)) as parsed:
            parsed.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise X1ProtocolError("Invalid X1 JPEG snapshot") from None


class X1ReadOnlyProtocol(XtoolProtocol):
    """Authenticated X1 status, inventory and snapshot reads."""

    def __init__(self, grant: X1Grant) -> None:
        super().__init__(grant.host)
        self._grant = grant
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session_token = ""
        self._auth_failed = False
        self._transaction = 0
        self._buffer = b""
        self._events: deque[dict[str, Any]] = deque()
        self._states: dict[str, str | None] = {}
        self._info: DeviceInfo | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return bool(
            self._ws is not None and not self._ws.closed and self._session_token
        )

    async def _open_socket(self, function: str) -> aiohttp.ClientWebSocketResponse:
        assert self._session is not None
        try:
            async with asyncio.timeout(_TIMEOUT):
                return await self._session.ws_connect(
                    f"wss://{self.host}:28900/websocket",
                    params={"id": self._grant.client_id, "function": function},
                    ssl=aiohttp.Fingerprint(bytes.fromhex(self._grant.leaf_sha256)),
                    origin="atomm://renderer",
                    timeout=aiohttp.ClientWSTimeout(ws_close=2),
                    max_msg_size=_MAX_FRAME,
                )
        except aiohttp.ServerFingerprintMismatch:
            self._auth_failed = True
            raise X1AuthError("X1 pinned certificate mismatch") from None
        except aiohttp.ClientError:
            raise X1ProtocolError("X1 WebSocket connection failed") from None

    async def connect(self) -> None:
        if self._auth_failed:
            raise X1AuthError("X1 authorization requires owner review")
        if self.connected:
            return
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(_refuse_redirect)
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
            trust_env=False,
            trace_configs=[trace],
        )
        try:
            self._ws = await self._open_socket("instruction")
            parity = await self._request(
                "/v1/user/parity",
                data={
                    "userID": self._grant.user_id,
                    "userKey": "bWFrZWJsb2NrLXh0b29s",
                    "timezone": self._grant.timezone,
                    "token": self._grant.token,
                },
            )
            if (
                not isinstance(parity, dict)
                or not isinstance(parity.get("sessionToken"), str)
                or not parity["sessionToken"]
            ):
                raise X1AuthError("X1 parity lacks a session token")
            if "token" in parity and parity["token"] != self._grant.token:
                raise X1AuthError("X1 permanent token changed; owner review required")
            self._session_token = parity["sessionToken"]
        except X1AuthError:
            self._auth_failed = True
            await self.disconnect()
            raise
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        try:
            if self._ws is not None:
                await self._ws.close()
        finally:
            self._ws = None
            self._session_token = ""
            self._buffer = b""
            self._events.clear()
            self._states = {}
            self._info = None
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def _event(self) -> dict[str, Any]:
        assert self._ws is not None
        while not self._events:
            message = await self._ws.receive()
            if message.type != aiohttp.WSMsgType.BINARY:
                raise X1ProtocolError(
                    "X1 instruction channel closed or returned a nonbinary frame"
                )
            frames, self._buffer = decode_frames(self._buffer + message.data)
            if len(self._buffer) > _MAX_FRAME + 10:
                raise X1ProtocolError("X1 receive buffer exceeds limit")
            for protocol, payload in frames:
                if protocol != 4:
                    raise X1ProtocolError("Unexpected X1 instruction protocol")
                try:
                    event = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise X1ProtocolError("Invalid X1 JSON frame") from None
                if not isinstance(event, dict):
                    raise X1ProtocolError("Invalid X1 event")
                self._events.append(event)
        return self._events.popleft()

    def _report(self, event: dict[str, Any]) -> None:
        if event.get("type") == "request" and event.get("method") == "REPORT":
            data = event.get("data")
            if isinstance(data, dict) and "/v1/platform/device/state" in data:
                self._states = parse_report(data["/v1/platform/device/state"])

    async def _request(
        self,
        url: str,
        method: str = "GET",
        *,
        params: dict[str, object] | None = None,
        data: dict[str, object] | None = None,
    ) -> object:
        assert self._ws is not None
        self._transaction = self._transaction % 65500 + 1
        transaction = self._transaction
        envelope = {
            "type": "request",
            "method": method,
            "url": url,
            "params": params if params is not None else {},
            "data": data if data is not None else {},
            "timestamp": int(time.time() * 1000),
            "transactionId": transaction,
        }
        if self._session_token:
            envelope["sessionToken"] = self._session_token
        async with asyncio.timeout(_TIMEOUT):
            await self._ws.send_bytes(
                encode_frame(json.dumps(envelope, separators=(",", ":")).encode())
            )
            while True:
                event = await self._event()
                self._report(event)
                if event.get("type") != "response":
                    continue
                inner = event.get("data")
                txn = event.get("transactionId")
                if txn is None and isinstance(inner, dict):
                    txn = inner.get("transactionId")
                if type(txn) is not int or txn != transaction:
                    raise X1ProtocolError("Unexpected X1 response transaction")
                code = event.get("code")
                if type(code) is not int:
                    raise X1ProtocolError("X1 response lacks a result code")
                result = event.get("data")
                if code == 0 and isinstance(result, dict) and "code" in result:
                    if type(result["code"]) is not int or "data" not in result:
                        raise X1ProtocolError("Invalid nested X1 result")
                    code, result = result["code"], result["data"]
                if code != 0:
                    if url == "/v1/user/parity" or code in (401, 403, 406, 409, 503):
                        self._auth_failed = True
                        raise X1AuthError("X1 authorization refused")
                    raise X1ProtocolError(f"X1 read refused with code {code}")
                if "data" not in event:
                    raise X1ProtocolError("X1 response lacks data")
                return content(result)

    async def get_device_info(self) -> DeviceInfo:
        if self._info is None:
            await self.connect()
            value = await self._request("/v1/platform/device/machine-info")
            if (
                not isinstance(value, dict)
                or value.get("sn") != self._grant.serial
                or value.get("deviceCode") != "JS001"
            ):
                self._auth_failed = True
                raise X1AuthError("X1 protected identity mismatch")
            firmware = value.get("firmware")
            if (
                not isinstance(firmware, dict)
                or not isinstance(firmware.get("version"), str)
                or not firmware["version"]
            ):
                raise X1ProtocolError("X1 machine info lacks firmware")
            self._info = DeviceInfo(
                serial_number=self._grant.serial, main_firmware=firmware["version"]
            )
        return self._info

    async def get_version(self) -> str:
        return (await self.get_device_info()).main_firmware

    async def poll_readonly(self) -> X1ReadOnlyState:
        async with self._lock:
            try:
                async with asyncio.timeout(45):
                    await self.connect()
                    info = await self.get_device_info()
                    await self._request("/v1/platform/device/state/sync", "POST")
                    cameras = parse_cameras(
                        await self._request("/v1/platform/camera/list")
                    )
                    modules = parse_modules(
                        await self._request("/v1/platform/accessories/list")
                    )
                    connected = None
                    dongle = modules.get(71)
                    if dongle is not None and dongle.online:
                        connected = parse_connected(
                            await self._request(
                                "/v1/platform/accessories/control",
                                "POST",
                                params={"id": 71},
                                data={"command": "M9098"},
                            )
                        )
                    # Reports can share a WS message with the last response.
                    while self._events:
                        self._report(self._events.popleft())
                    return X1ReadOnlyState(
                        available=True,
                        serial_number=info.serial_number,
                        firmware_version=info.main_firmware,
                        raw_states=dict(self._states),
                        cameras=cameras,
                        modules=modules,
                        bluetooth=connected,
                    )
            finally:
                await self.disconnect()

    async def snapshot(self, name: str) -> bytes:
        if name not in ("far", "near", "side"):
            raise X1ProtocolError("X1 camera is not admitted for snapshots")
        async with self._lock:
            try:
                async with asyncio.timeout(60):
                    await self.connect()
                    await self.get_device_info()
                    cameras = parse_cameras(
                        await self._request("/v1/platform/camera/list")
                    )
                    if name not in cameras:
                        raise X1ProtocolError("X1 camera is absent")
                    snap = await self._request(
                        "/v1/platform/camera/snap", "POST", params={"name": name}
                    )
                    if (
                        not isinstance(snap, dict)
                        or not isinstance(snap.get("filename"), str)
                        or not snap["filename"]
                    ):
                        raise X1ProtocolError("X1 snapshot lacks an issued filename")
                    ws = await self._open_socket("file_stream")
                    try:
                        return await self._download_snapshot(
                            ws, snap["filename"], cameras[name]
                        )
                    finally:
                        await ws.close()
            finally:
                await self.disconnect()

    async def _download_snapshot(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        filename: str,
        camera: X1CameraInfo,
    ) -> bytes:
        channel = 1
        meta = await self._request(
            "/v1/filetransfer/download",
            "PUT",
            data={
                "filetype": 5,
                "filename": filename,
                "digesttype": 1,
                "channel": channel,
                "packetsize": 1048576,
            },
        )
        if (
            not isinstance(meta, dict)
            or type(meta.get("filesize")) is not int
            or not 0 < meta["filesize"] <= 24 * 1024 * 1024
            or type(meta.get("digesttype")) is not int
            or meta["digesttype"] != 1
            or not isinstance(meta.get("digestdata"), str)
            or re.fullmatch(r"[0-9a-fA-F]{32}", meta["digestdata"]) is None
            or type(meta.get("packetsize")) is not int
            or not 0 < meta["packetsize"] <= 1048576
        ):
            raise X1ProtocolError("Invalid X1 snapshot transfer metadata")
        size, packet_size = meta["filesize"], meta["packetsize"]
        image = bytearray()
        pending = b""
        while len(image) < size:
            end = len(image) + min(packet_size, size - len(image))
            request = (
                bytes([1, channel])
                + len(image).to_bytes(5, "big")
                + (end - len(image)).to_bytes(3, "big")
            )
            await ws.send_bytes(encode_frame(request, 33, checksum=False))
            while len(image) < end:
                message = await ws.receive()
                if message.type != aiohttp.WSMsgType.BINARY:
                    raise X1ProtocolError(
                        "X1 snapshot file channel closed or nonbinary"
                    )
                frames, pending = decode_frames(pending + message.data)
                for protocol, payload in frames:
                    if protocol != 33:
                        raise X1ProtocolError("Unexpected X1 file protocol")
                    if payload == b"\x82":
                        continue
                    if (
                        len(payload) < 8
                        or payload[:2] != bytes([129, channel])
                        or int.from_bytes(payload[2:7], "big") != len(image)
                        or len(image) + len(payload) - 7 > end
                    ):
                        raise X1ProtocolError(
                            "Invalid X1 snapshot offset, size or channel"
                        )
                    image.extend(payload[7:])
        if (
            pending
            or len(image) != size
            or hashlib.md5(image).hexdigest().lower() != meta["digestdata"].lower()
        ):
            raise X1ProtocolError("X1 snapshot digest or size mismatch")
        result = bytes(image)
        await asyncio.to_thread(_verify_jpeg, result, camera)
        await self._request(
            "/v1/filetransfer/finish",
            "PUT",
            data={
                "code": 0,
                "message": "file transfer finish",
                "channel": channel,
            },
        )
        return result
