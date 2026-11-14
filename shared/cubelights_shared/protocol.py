"""Transport-agnostic dual-framing wire protocol for Cubelights.

Supports multiplexing UTF-8 JSON-line commands/responses (starting with '{' / 0x7B)
and compact binary RGB frame packets (starting with 0x01) on the same byte stream.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

DEFAULT_PAN_IP = "10.55.0.1"
DEFAULT_TCP_PORT = 7777
DEFAULT_UDP_PORT = 7778
RFCOMM_CONSOLE_CHANNEL = 1
RFCOMM_CONTROL_CHANNEL = 2

SPP_SERIAL_UUID = "00001101-0000-1000-8000-00805f9b34fb"
CUBELIGHTS_CONTROL_UUID = "e3b0c442-1a2b-4c3d-9e4f-100000000001"

FRAME_MAGIC = 0x01
FRAME_HEADER_STRUCT = struct.Struct("!BBBH")  # magic, flags, brightness (0-255), payload_len
FRAME_FLAG_NONE = 0x00
FRAME_FLAG_PERSIST_STATIC = 0x01

MAX_JSON_LINE_BYTES = 256 * 1024
MAX_BINARY_PAYLOAD_BYTES = 4096


class ProtocolError(ValueError):
    """Raised when an incoming stream contains malformed framing or JSON."""


@dataclass(slots=True, frozen=True)
class FramePacket:
    """Compact binary frame packet for low-latency direct LED control."""

    rgb_bytes: bytes
    brightness: int = 255
    flags: int = FRAME_FLAG_NONE

    def __post_init__(self) -> None:
        if not (0 <= self.brightness <= 255):
            raise ValueError("brightness must be between 0 and 255")
        if len(self.rgb_bytes) % 3 != 0:
            raise ValueError("rgb_bytes length must be a multiple of 3")
        if len(self.rgb_bytes) > MAX_BINARY_PAYLOAD_BYTES:
            raise ValueError("rgb_bytes exceeds MAX_BINARY_PAYLOAD_BYTES")

    def encode(self) -> bytes:
        """Encode packet to wire bytes."""
        header = FRAME_HEADER_STRUCT.pack(
            FRAME_MAGIC,
            self.flags & 0xFF,
            self.brightness & 0xFF,
            len(self.rgb_bytes),
        )
        return header + self.rgb_bytes

    def to_rgb_list(self) -> list[tuple[int, int, int]]:
        """Unpack flat RGB byte payload into a list of (R, G, B) tuples."""
        data = self.rgb_bytes
        return [(data[i], data[i + 1], data[i + 2]) for i in range(0, len(data), 3)]

    @classmethod
    def from_rgb_list(
        cls,
        colors: list[tuple[int, int, int]],
        brightness: int = 255,
        flags: int = FRAME_FLAG_NONE,
    ) -> FramePacket:
        """Pack a sequence of (R, G, B) tuples into a FramePacket."""
        buf = bytearray(len(colors) * 3)
        for idx, (r, g, b) in enumerate(colors):
            off = idx * 3
            buf[off] = max(0, min(255, int(r)))
            buf[off + 1] = max(0, min(255, int(g)))
            buf[off + 2] = max(0, min(255, int(b)))
        return cls(rgb_bytes=bytes(buf), brightness=brightness, flags=flags)

    @classmethod
    def decode_datagram(cls, data: bytes) -> FramePacket:
        """Decode a standalone UDP datagram containing exactly one FramePacket."""
        if len(data) < FRAME_HEADER_STRUCT.size:
            raise ProtocolError("UDP frame datagram too short")
        magic, flags, brightness, payload_len = FRAME_HEADER_STRUCT.unpack_from(data, 0)
        if magic != FRAME_MAGIC:
            raise ProtocolError(f"Invalid frame magic byte: 0x{magic:02x}")
        expected_len = FRAME_HEADER_STRUCT.size + payload_len
        if len(data) != expected_len:
            raise ProtocolError(f"Datagram length mismatch: got {len(data)}, expected {expected_len}")
        payload = data[FRAME_HEADER_STRUCT.size :]
        if payload_len % 3 != 0:
            raise ProtocolError("Frame payload length is not a multiple of 3")
        return cls(rgb_bytes=payload, brightness=brightness, flags=flags)


def encode_json_command(cmd: str, req_id: str | None = None, **payload: Any) -> bytes:
    """Encode a JSON-line command message."""
    msg: dict[str, Any] = {"cmd": cmd}
    if req_id is not None:
        msg["id"] = req_id
    msg.update(payload)
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


def encode_json_response(
    ok: bool,
    req_id: str | None = None,
    data: dict[str, Any] | None = None,
    error: str | None = None,
) -> bytes:
    """Encode a JSON-line response message."""
    msg: dict[str, Any] = {"ok": ok}
    if req_id is not None:
        msg["id"] = req_id
    if data is not None:
        msg["data"] = data
    if error is not None:
        msg["error"] = error
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


class StreamDecoder:
    """Incremental dual-framing decoder for mixed JSON lines and binary FramePackets."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def clear(self) -> None:
        """Discard buffered bytes."""
        self._buf.clear()

    def feed(self, chunk: bytes) -> list[dict[str, Any] | FramePacket]:
        """Feed incoming stream bytes and return complete decoded messages."""
        if not chunk:
            return []
        self._buf.extend(chunk)
        items: list[dict[str, Any] | FramePacket] = []

        while self._buf:
            # Skip whitespace/newlines between frames/lines
            while self._buf and self._buf[0] in (0x0A, 0x0D, 0x20, 0x09):
                del self._buf[0]
            if not self._buf:
                break

            lead = self._buf[0]
            if lead == FRAME_MAGIC:
                if len(self._buf) < FRAME_HEADER_STRUCT.size:
                    break
                _, flags, brightness, payload_len = FRAME_HEADER_STRUCT.unpack_from(self._buf, 0)
                if payload_len > MAX_BINARY_PAYLOAD_BYTES or payload_len % 3 != 0:
                    del self._buf[0]
                    raise ProtocolError(f"Invalid binary frame payload_len={payload_len}")
                total_len = FRAME_HEADER_STRUCT.size + payload_len
                if len(self._buf) < total_len:
                    break
                payload = bytes(self._buf[FRAME_HEADER_STRUCT.size : total_len])
                del self._buf[:total_len]
                items.append(FramePacket(rgb_bytes=payload, brightness=brightness, flags=flags))
            elif lead == 0x7B:  # '{'
                nl_idx = self._buf.find(b"\n")
                if nl_idx == -1:
                    if len(self._buf) > MAX_JSON_LINE_BYTES:
                        self._buf.clear()
                        raise ProtocolError("JSON line exceeded MAX_JSON_LINE_BYTES")
                    break
                raw_line = bytes(self._buf[:nl_idx]).strip()
                del self._buf[: nl_idx + 1]
                try:
                    obj = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProtocolError(f"Malformed JSON message: {exc}") from exc
                if not isinstance(obj, dict):
                    raise ProtocolError("JSON message must be a JSON object")
                items.append(obj)
            else:
                bad_byte = self._buf[0]
                del self._buf[0]
                raise ProtocolError(f"Unexpected framing byte: 0x{bad_byte:02x}")

        return items
