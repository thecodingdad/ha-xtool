"""X1 CRC framing without importing the legacy transport or Home Assistant."""

from .x1_data import X1ProtocolError

MAX_FRAME = 2 * 1024 * 1024


def _entry(value: int) -> int:
    for _ in range(8):
        value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


_TABLE = tuple(_entry(value) for value in range(256))


def crc16(data: bytes) -> int:
    crc = 0
    for value in data:
        crc = _TABLE[(crc ^ value) & 0xFF] ^ (crc >> 8)
    return crc


def encode_frame(payload: bytes, protocol: int = 4, *, checksum: bool = True) -> bytes:
    if len(payload) > MAX_FRAME:
        raise X1ProtocolError("X1 frame exceeds limit")
    header = (
        b"\xba\xbe"
        + len(payload).to_bytes(3, "big")
        + bytes([protocol | (0 if checksum else 0x80)])
        + (crc16(payload) if checksum else 0).to_bytes(2, "big")
    )
    return header + crc16(header).to_bytes(2, "big") + payload


def decode_frames(buffer: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """Reject corruption instead of resynchronizing past it."""
    frames = []
    while len(buffer) >= 10:
        header = buffer[:10]
        size = int.from_bytes(header[2:5], "big")
        if header[:2] != b"\xba\xbe" or crc16(header[:8]) != int.from_bytes(
            header[8:10], "big"
        ):
            raise X1ProtocolError("Invalid X1 frame header")
        if size > MAX_FRAME:
            raise X1ProtocolError("X1 frame exceeds limit")
        if len(buffer) < size + 10:
            break
        payload = buffer[10 : size + 10]
        if not header[5] & 0x80 and crc16(payload) != int.from_bytes(
            header[6:8], "big"
        ):
            raise X1ProtocolError("Invalid X1 frame checksum")
        frames.append((header[5] & 0x7F, payload))
        buffer = buffer[size + 10 :]
    return frames, buffer
