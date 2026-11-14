"""Pimoroni Button SHIM driver (TCA9554A I2C expander + onboard APA102 status LED)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from cubelights_shared.patterns import RGB

logger = logging.getLogger(__name__)

BUTTON_NAMES = ("A", "B", "C", "D", "E")
_BUTTON_BITS = {name: idx for idx, name in enumerate(BUTTON_NAMES)}

_I2C_ADDR = 0x3F
_REG_INPUT = 0x00
_REG_OUTPUT = 0x01
_REG_POLARITY = 0x02
_REG_CONFIG = 0x03

_LED_CLK_BIT = 0x40  # Bit 6
_LED_DAT_BIT = 0x80  # Bit 7


@dataclass(slots=True, frozen=True)
class ButtonEvent:
    """Normalized button action event."""

    kind: str  # "short", "long", "chord"
    buttons: frozenset[str]


ButtonCallback = Callable[[ButtonEvent], None]


class ButtonShimDriver(Protocol):
    """Protocol for physical and mocked Button SHIM hardware."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def set_pixel(self, r: int, g: int, b: int, brightness: float = 0.25) -> None: ...

    def register_callback(self, cb: ButtonCallback) -> None: ...


class _ButtonStateMachine:
    """Tracks press/release timing, debounce, long-press, and multi-button chords."""

    def __init__(
        self,
        long_press_s: float = 1.8,
        chord_press_s: float = 2.8,
        chord_buttons: frozenset[str] = frozenset({"A", "E"}),
    ) -> None:
        self.long_press_s = long_press_s
        self.chord_press_s = chord_press_s
        self.chord_buttons = chord_buttons
        self._down_since: dict[str, float] = {}
        self._long_fired: set[str] = set()
        self._chord_since: float | None = None
        self._chord_fired: bool = False
        self._suppressed_release: set[str] = set()

    def update(self, pressed_buttons: set[str], now: float) -> list[ButtonEvent]:
        events: list[ButtonEvent] = []

        # Handle newly pressed buttons
        for btn in pressed_buttons:
            if btn not in self._down_since:
                self._down_since[btn] = now

        # Check multi-button chord (e.g. A+E held for safe shutdown)
        if self.chord_buttons.issubset(pressed_buttons):
            if self._chord_since is None:
                self._chord_since = now
            elif not self._chord_fired and (now - self._chord_since) >= self.chord_press_s:
                self._chord_fired = True
                self._suppressed_release.update(self.chord_buttons)
                self._long_fired.update(self.chord_buttons)
                events.append(ButtonEvent(kind="chord", buttons=self.chord_buttons))
        else:
            self._chord_since = None
            if not pressed_buttons:
                self._chord_fired = False

        # Check single-button long press (suppress if chord is forming)
        if len(pressed_buttons) == 1:
            btn = next(iter(pressed_buttons))
            if (
                btn not in self._long_fired
                and btn not in self._suppressed_release
                and (now - self._down_since[btn]) >= self.long_press_s
            ):
                self._long_fired.add(btn)
                self._suppressed_release.add(btn)
                events.append(ButtonEvent(kind="long", buttons=frozenset({btn})))

        # Handle releases -> emit short press if neither long nor chord fired
        released = [btn for btn in list(self._down_since.keys()) if btn not in pressed_buttons]
        for btn in released:
            self._down_since.pop(btn, None)
            was_long = btn in self._long_fired
            was_suppressed = btn in self._suppressed_release
            self._long_fired.discard(btn)
            self._suppressed_release.discard(btn)
            if not was_long and not was_suppressed:
                events.append(ButtonEvent(kind="short", buttons=frozenset({btn})))

        return events


class HardwareButtonShimDriver:
    """I2C driver for Pimoroni Button SHIM on Raspberry Pi (/dev/i2c-1, 0x3F)."""

    def __init__(
        self,
        i2c_bus: int = 1,
        address: int = _I2C_ADDR,
        poll_interval_s: float = 0.02,
        long_press_s: float = 1.8,
        chord_press_s: float = 2.8,
    ) -> None:
        self._address = address
        self._poll_interval_s = poll_interval_s
        self._callbacks: list[ButtonCallback] = []
        self._sm = _ButtonStateMachine(long_press_s=long_press_s, chord_press_s=chord_press_s)
        self._lock = threading.RLock()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._bus = self._open_smbus(i2c_bus)
        self._init_chip()

    @staticmethod
    def _open_smbus(bus_num: int):
        try:
            import smbus2  # type: ignore[import-not-found]

            return smbus2.SMBus(bus_num)
        except ImportError:
            import smbus  # type: ignore[import-not-found]

            return smbus.SMBus(bus_num)

    def _init_chip(self) -> None:
        with self._lock:
            # Bits 0..4 input (buttons), bits 5..7 output (unused + LED clk/dat)
            self._bus.write_byte_data(self._address, _REG_CONFIG, 0b0001_1111)
            self._bus.write_byte_data(self._address, _REG_POLARITY, 0b0000_0000)
            self._bus.write_byte_data(self._address, _REG_OUTPUT, 0b0000_0000)
        self.set_pixel(0, 0, 0, 0.0)

    def _write_byte(self, byte_val: int) -> None:
        for _ in range(8):
            out = _LED_DAT_BIT if (byte_val & 0x80) else 0
            self._bus.write_byte_data(self._address, _REG_OUTPUT, out)
            self._bus.write_byte_data(self._address, _REG_OUTPUT, out | _LED_CLK_BIT)
            byte_val <<= 1

    def set_pixel(self, r: int, g: int, b: int, brightness: float = 0.25) -> None:
        """Set the Button SHIM onboard APA102 RGB status LED."""
        bri = max(0, min(31, int(round(max(0.0, min(1.0, brightness)) * 31))))
        with self._lock:
            try:
                for _ in range(4):
                    self._write_byte(0x00)
                self._write_byte(0xE0 | bri)
                self._write_byte(max(0, min(255, int(b))))
                self._write_byte(max(0, min(255, int(g))))
                self._write_byte(max(0, min(255, int(r))))
                for _ in range(4):
                    self._write_byte(0xFF)
            except OSError as exc:
                logger.warning("Button SHIM I2C write failed: %s", exc)

    def register_callback(self, cb: ButtonCallback) -> None:
        self._callbacks.append(cb)

    def _read_pressed(self) -> set[str]:
        with self._lock:
            state = self._bus.read_byte_data(self._address, _REG_INPUT)
        # Active-low buttons on bits 0..4
        return {name for name, bit in _BUTTON_BITS.items() if not (state & (1 << bit))}

    def _poll_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                pressed = self._read_pressed()
                events = self._sm.update(pressed, time.monotonic())
                for evt in events:
                    for cb in self._callbacks:
                        try:
                            cb(evt)
                        except Exception:
                            logger.exception("Error in Button SHIM callback")
            except OSError as exc:
                logger.warning("Button SHIM I2C read error: %s", exc)
            self._stop_evt.wait(self._poll_interval_s)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="button-shim-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.set_pixel(0, 0, 0, 0.0)
        if hasattr(self._bus, "close"):
            self._bus.close()


class MockButtonShimDriver:
    """In-memory Button SHIM double for deterministic unit testing."""

    def __init__(self, long_press_s: float = 1.8, chord_press_s: float = 2.8) -> None:
        self._sm = _ButtonStateMachine(long_press_s=long_press_s, chord_press_s=chord_press_s)
        self._callbacks: list[ButtonCallback] = []
        self._held: set[str] = set()
        self.last_pixel: tuple[RGB, float] = ((0, 0, 0), 0.0)
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False
        self.last_pixel = ((0, 0, 0), 0.0)

    def set_pixel(self, r: int, g: int, b: int, brightness: float = 0.25) -> None:
        self.last_pixel = ((r, g, b), brightness)

    def register_callback(self, cb: ButtonCallback) -> None:
        self._callbacks.append(cb)

    def step(self, pressed: set[str], now: float) -> list[ButtonEvent]:
        self._held = set(pressed)
        events = self._sm.update(self._held, now)
        for evt in events:
            for cb in self._callbacks:
                cb(evt)
        return events
