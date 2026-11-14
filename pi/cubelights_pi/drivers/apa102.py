"""APA102 / SK9822 SPI LED strip driver for Maniacal Labs PiPixel HAT."""

from __future__ import annotations

import math
from typing import Protocol

from cubelights_shared.patterns import RGB

_CHANNEL_MAP = {"R": 0, "G": 1, "B": 2}


class StripDriver(Protocol):
    """Protocol implemented by physical and mock LED strip drivers."""

    @property
    def num_leds(self) -> int: ...

    def show(self, pixels: list[RGB], brightness: float = 1.0) -> None: ...

    def clear(self) -> None: ...

    def close(self) -> None: ...


class APA102Driver:
    """Pure-Python spidev driver for APA102 / SK9822 strips on PiPixel SPI_DAT."""

    def __init__(
        self,
        num_leds: int = 29,
        spi_bus: int = 0,
        spi_device: int = 0,
        spi_speed_hz: int = 4_000_000,
        color_order: str = "BGR",
        led_protocol: str = "apa102",
        max_brightness_cap: float = 0.75,
        spi_client: object | None = None,
    ) -> None:
        self._num_leds = num_leds
        self.color_order = color_order.upper()
        self.led_protocol = led_protocol.lower()
        self.max_brightness_cap = max(0.05, min(1.0, float(max_brightness_cap)))
        self._order_indices = tuple(_CHANNEL_MAP[ch] for ch in self.color_order)

        if spi_client is not None:
            self._spi = spi_client
        else:
            import spidev  # type: ignore[import-not-found]

            spi = spidev.SpiDev()
            spi.open(spi_bus, spi_device)
            spi.max_speed_hz = spi_speed_hz
            spi.mode = 0
            self._spi = spi

    @property
    def num_leds(self) -> int:
        return self._num_leds

    def set_num_leds(self, count: int) -> None:
        self._num_leds = max(1, int(count))

    def set_color_order(self, color_order: str) -> None:
        order = color_order.upper()
        if set(order) != {"R", "G", "B"} or len(order) != 3:
            raise ValueError(f"Invalid color_order: {color_order!r}")
        self.color_order = order
        self._order_indices = tuple(_CHANNEL_MAP[ch] for ch in order)

    def build_frame_bytes(self, pixels: list[RGB], brightness: float = 1.0) -> bytearray:
        """Build complete SPI frame including start, per-LED global brightness, and end frames."""
        effective = max(0.0, min(1.0, float(brightness))) * self.max_brightness_cap
        if effective <= 0.0:
            hw_level = 0
            rgb_scale = 0.0
        else:
            # Combine 5-bit APA102 global current control with 8-bit PWM scaling
            # to avoid visible stepping at low cubicle brightness levels.
            exact_level = effective * 31.0
            hw_level = max(1, min(31, math.ceil(exact_level)))
            rgb_scale = exact_level / hw_level

        header_byte = 0xE0 | (hw_level & 0x1F)
        end_len = max(4, math.ceil(self._num_leds / 16))
        if self.led_protocol == "sk9822":
            # SK9822 latches on 0x00 reset frame + clock pulses
            end_bytes = b"\x00" * (4 + end_len)
        else:
            end_bytes = b"\xff" * end_len

        frame = bytearray(4 + self._num_leds * 4 + len(end_bytes))
        # Start frame is 4 zero bytes (already 0x00 in bytearray)
        i0, i1, i2 = self._order_indices
        for idx in range(self._num_leds):
            rgb = pixels[idx] if idx < len(pixels) else (0, 0, 0)
            off = 4 + idx * 4
            frame[off] = header_byte
            frame[off + 1] = max(0, min(255, int(rgb[i0] * rgb_scale)))
            frame[off + 2] = max(0, min(255, int(rgb[i1] * rgb_scale)))
            frame[off + 3] = max(0, min(255, int(rgb[i2] * rgb_scale)))

        frame[4 + self._num_leds * 4 :] = end_bytes
        return frame

    def show(self, pixels: list[RGB], brightness: float = 1.0) -> None:
        buf = self.build_frame_bytes(pixels, brightness=brightness)
        self._spi.xfer2(list(buf))  # type: ignore[attr-defined]

    def clear(self) -> None:
        self.show([(0, 0, 0)] * self._num_leds, brightness=0.0)

    def close(self) -> None:
        if hasattr(self._spi, "close"):
            self._spi.close()  # type: ignore[attr-defined]


class MockStripDriver:
    """In-memory LED strip double for automated tests and local dev."""

    def __init__(self, num_leds: int = 29, max_brightness_cap: float = 0.75) -> None:
        self._num_leds = num_leds
        self.max_brightness_cap = max_brightness_cap
        self.color_order = "BGR"
        self.last_frame: list[RGB] = [(0, 0, 0)] * num_leds
        self.last_brightness: float = 0.0
        self.frame_count: int = 0
        self.history: list[tuple[list[RGB], float]] = []

    @property
    def num_leds(self) -> int:
        return self._num_leds

    def set_num_leds(self, count: int) -> None:
        self._num_leds = max(1, int(count))

    def set_color_order(self, color_order: str) -> None:
        self.color_order = color_order.upper()

    def show(self, pixels: list[RGB], brightness: float = 1.0) -> None:
        padded = list(pixels[: self._num_leds])
        if len(padded) < self._num_leds:
            padded.extend([(0, 0, 0)] * (self._num_leds - len(padded)))
        self.last_frame = padded
        self.last_brightness = max(0.0, min(1.0, float(brightness))) * self.max_brightness_cap
        self.frame_count += 1
        self.history.append((padded, self.last_brightness))
        if len(self.history) > 120:
            del self.history[0]

    def clear(self) -> None:
        self.show([(0, 0, 0)] * self._num_leds, brightness=0.0)

    def close(self) -> None:
        pass
