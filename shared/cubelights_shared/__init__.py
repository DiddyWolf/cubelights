"""Shared protocol, segment mapping, and pattern definitions for Cubelights."""

from .patterns import Keyframe, KeyframePattern, KeyframeTarget, clamp_rgb, ease_value, parse_color
from .protocol import (
    CUBELIGHTS_CONTROL_UUID,
    DEFAULT_PAN_IP,
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    FRAME_FLAG_NONE,
    FRAME_FLAG_PERSIST_STATIC,
    FRAME_MAGIC,
    FramePacket,
    ProtocolError,
    RFCOMM_CONSOLE_CHANNEL,
    RFCOMM_CONTROL_CHANNEL,
    SPP_SERIAL_UUID,
    StreamDecoder,
    encode_json_command,
    encode_json_response,
)
from .segments import LedPoint, SegmentMap, SegmentSpec, default_segment_map

__all__ = [
    "CUBELIGHTS_CONTROL_UUID",
    "DEFAULT_PAN_IP",
    "DEFAULT_TCP_PORT",
    "DEFAULT_UDP_PORT",
    "FRAME_FLAG_NONE",
    "FRAME_FLAG_PERSIST_STATIC",
    "FRAME_MAGIC",
    "FramePacket",
    "Keyframe",
    "KeyframePattern",
    "KeyframeTarget",
    "LedPoint",
    "ProtocolError",
    "RFCOMM_CONSOLE_CHANNEL",
    "RFCOMM_CONTROL_CHANNEL",
    "SPP_SERIAL_UUID",
    "SegmentMap",
    "SegmentSpec",
    "StreamDecoder",
    "clamp_rgb",
    "default_segment_map",
    "ease_value",
    "encode_json_command",
    "encode_json_response",
    "parse_color",
]
