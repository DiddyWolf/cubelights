"""Tests for APA102 driver encoding, shaders, and LedEngine layer priority."""

import datetime

from cubelights_shared.patterns import Keyframe, KeyframePattern, KeyframeTarget
from cubelights_shared.segments import default_segment_map

from cubelights_pi.config import DeviceConfig, ModeState
from cubelights_pi.drivers.apa102 import APA102Driver, MockStripDriver
from cubelights_pi.drivers.button_shim import MockButtonShimDriver
from cubelights_pi.engine import LedEngine
from cubelights_pi.shaders import KeyframePlayer, resolve_seasonal_theme


class _FakeSpi:
    def __init__(self) -> None:
        self.last_tx: list[int] = []

    def xfer2(self, data: list[int]) -> list[int]:
        self.last_tx = data
        return data


def test_apa102_frame_encoding_bgr_order_and_brightness_cap():
    spi = _FakeSpi()
    drv = APA102Driver(
        num_leds=2,
        color_order="BGR",
        led_protocol="apa102",
        max_brightness_cap=0.5,
        spi_client=spi,
    )
    drv.show([(200, 100, 50), (0, 255, 0)], brightness=1.0)
    # 4 start bytes + 2*4 LED bytes + 4 end bytes = 16 bytes
    assert len(spi.last_tx) == 16
    assert spi.last_tx[:4] == [0, 0, 0, 0]
    # LED 0 header byte has 0xE0 | hw_level
    assert (spi.last_tx[4] & 0xE0) == 0xE0
    # In BGR order, B <= G <= R for (200, 100, 50)
    assert spi.last_tx[5] < spi.last_tx[6] < spi.last_tx[7]
    assert spi.last_tx[-4:] == [255, 255, 255, 255]


def test_seasonal_auto_resolution():
    assert resolve_seasonal_theme("auto", datetime.datetime(2026, 10, 15)) == "halloween"
    assert resolve_seasonal_theme("auto", datetime.datetime(2026, 12, 20)) == "holiday"
    assert resolve_seasonal_theme("auto", datetime.datetime(2026, 4, 10)) == "spring"
    assert resolve_seasonal_theme("auto", datetime.datetime(2026, 7, 4)) == "summer"
    assert resolve_seasonal_theme("auto", datetime.datetime(2026, 11, 13)) == "fall"


def test_led_engine_layer_precedence_and_stream_timeout():
    cfg = DeviceConfig()
    state = ModeState(active_mode="solid", mode_params={"color": "#0000FF"})
    strip = MockStripDriver(num_leds=29, max_brightness_cap=cfg.max_brightness_cap)
    shim = MockButtonShimDriver()
    engine = LedEngine(config=cfg, state=state, strip=strip, shim=shim)

    # 1. Autonomous solid blue
    px = engine.render_tick(now=10.0)
    assert px == [(0, 0, 255)] * 29

    # 2. Busy override takes precedence over autonomous mode
    engine.set_busy(True)
    px = engine.render_tick(now=10.5)
    assert all(r >= g and r >= b for r, g, b in px)

    # 3. Live binary stream takes precedence even over Busy until timeout
    green_frame = [(0, 255, 0)] * 29
    engine.push_stream_frame(green_frame, brightness=1.0, now=11.0)
    px = engine.render_tick(now=12.0)
    assert px == green_frame

    # 4. After stream_timeout_s (5.0s), reverts cleanly to Busy red
    px = engine.render_tick(now=17.0)
    assert px != green_frame
    assert all(r >= b for r, _g, b in px)

    # 5. Power off clears strip
    engine.set_power(False)
    px = engine.render_tick(now=18.0)
    assert px == [(0, 0, 0)] * 29
    assert strip.last_brightness == 0.0


def test_keyframe_player_interpolation():
    seg_map = default_segment_map()
    pat = KeyframePattern(
        name="fade",
        loop=0,
        keyframes=[
            Keyframe(duration_ms=1000, easing="linear", fill=(0, 0, 0)),
            Keyframe(
                duration_ms=1000,
                easing="linear",
                fill=(0, 0, 0),
                targets=[KeyframeTarget(zone="strip_1", color=(200, 100, 0))],
            ),
        ],
    )
    player = KeyframePlayer(pat, seg_map)
    mid = player.render(0.5)
    assert mid[0] == (100, 50, 0)
    assert mid[10] == (0, 0, 0)


def test_shim_led_startup_pairing_and_bluetooth_status(monkeypatch):
    cfg = DeviceConfig()
    state = ModeState(active_mode="solid", mode_params={"color": "#FFFFFF"})
    strip = MockStripDriver(num_leds=29, max_brightness_cap=cfg.max_brightness_cap)
    shim = MockButtonShimDriver()

    monkeypatch.setattr("cubelights_pi.engine.time.monotonic", lambda: 100.0)
    engine = LedEngine(config=cfg, state=state, strip=strip, shim=shim)

    # 1. Startup ready pattern (0..3.0s): green blinks then steady green
    engine.render_tick(now=100.1)
    assert shim.last_pixel[0] == (0, 255, 80)
    engine.render_tick(now=100.5)
    assert shim.last_pixel == ((0, 0, 0), 0.0)
    engine.render_tick(now=102.6)
    assert shim.last_pixel[0] == (0, 255, 80)

    # 2. After startup: BT unavailable -> dim amber
    engine.render_tick(now=104.0)
    assert shim.last_pixel[0] == (255, 110, 0)

    # 3. BT ready, disconnected -> breathing blue
    engine.set_bluetooth_status(ready=True, connected=False)
    engine.render_tick(now=104.0)
    assert shim.last_pixel[0] == (0, 60, 220)

    # 4. BT connected -> solid blue (even when strip power is off)
    engine.set_bluetooth_status(ready=True, connected=True)
    engine.set_power(False)
    engine._flash_until = 0.0
    engine.render_tick(now=104.5)
    assert shim.last_pixel == ((0, 90, 255), cfg.shim_led_brightness)

    # 5. Pairing mode -> alternating blue and red flash
    engine.set_pairing_indicator(True, duration_s=120.0)
    engine.render_tick(now=105.0)  # int(105.0 * 4) % 2 == 0 -> blue
    assert shim.last_pixel[0] == (0, 40, 255)
    engine.render_tick(now=105.25)  # int(105.25 * 4) % 2 == 1 -> red
    assert shim.last_pixel[0] == (255, 0, 20)
