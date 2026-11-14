"""Tests for Pimoroni Button SHIM state machine (short, long, chord A+E)."""

from cubelights_pi.drivers.button_shim import ButtonEvent, MockButtonShimDriver


def test_short_press_long_press_and_chord_events():
    shim = MockButtonShimDriver(long_press_s=1.8, chord_press_s=2.8)
    received: list[ButtonEvent] = []
    shim.register_callback(received.append)

    # Short press A
    shim.step({"A"}, 1.0)
    shim.step(set(), 1.2)
    assert received[-1] == ButtonEvent(kind="short", buttons=frozenset({"A"}))

    # Long press E (pairing) should not also emit a short press on release
    shim.step({"E"}, 2.0)
    assert shim.step({"E"}, 3.9) == [ButtonEvent(kind="long", buttons=frozenset({"E"}))]
    assert shim.step(set(), 4.1) == []

    # Chord A+E (safe shutdown) should suppress individual short/long presses
    shim.step({"A", "E"}, 10.0)
    assert shim.step({"A", "E"}, 12.9) == [ButtonEvent(kind="chord", buttons=frozenset({"A", "E"}))]
    assert shim.step(set(), 13.1) == []
