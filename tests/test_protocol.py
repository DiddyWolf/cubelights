"""Tests for dual-framing JSON + binary FramePacket wire protocol."""

import pytest

from cubelights_shared.protocol import (
    FRAME_FLAG_PERSIST_STATIC,
    FramePacket,
    ProtocolError,
    StreamDecoder,
    encode_json_command,
    encode_json_response,
)


def test_frame_packet_round_trip():
    colors = [(i * 8 % 256, (i * 17) % 256, (255 - i * 7) % 256) for i in range(29)]
    pkt = FramePacket.from_rgb_list(colors, brightness=200, flags=FRAME_FLAG_PERSIST_STATIC)
    encoded = pkt.encode()
    assert len(encoded) == 5 + 29 * 3

    decoded = FramePacket.decode_datagram(encoded)
    assert decoded.brightness == 200
    assert decoded.flags == FRAME_FLAG_PERSIST_STATIC
    assert decoded.to_rgb_list() == colors


def test_stream_decoder_mixed_chunked_messages():
    cmd1 = encode_json_command("set_mode", req_id="r1", name="rainbow", params={"speed": 0.1})
    pkt = FramePacket.from_rgb_list([(255, 0, 0)] * 29, brightness=128)
    resp = encode_json_response(True, req_id="r1", data={"active_mode": "rainbow"})
    combined = cmd1 + b"\r\n" + pkt.encode() + b"  " + resp

    decoder = StreamDecoder()
    decoded = []
    # Feed 3 bytes at a time to verify incremental reassembly across arbitrary boundaries
    for idx in range(0, len(combined), 3):
        decoded.extend(decoder.feed(combined[idx : idx + 3]))

    assert len(decoded) == 3
    assert decoded[0] == {"cmd": "set_mode", "id": "r1", "name": "rainbow", "params": {"speed": 0.1}}
    assert isinstance(decoded[1], FramePacket)
    assert decoded[1].to_rgb_list() == [(255, 0, 0)] * 29
    assert decoded[2] == {"ok": True, "id": "r1", "data": {"active_mode": "rainbow"}}


def test_stream_decoder_rejects_bad_framing_byte():
    decoder = StreamDecoder()
    with pytest.raises(ProtocolError):
        decoder.feed(b"\x99bad")
