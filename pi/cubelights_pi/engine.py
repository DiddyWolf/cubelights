"""Layered LED rendering engine coordinating modes, overrides, streaming, and Button SHIM feedback."""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

from cubelights_shared.patterns import KeyframePattern, RGB, clamp_rgb, parse_color
from cubelights_shared.segments import SegmentMap, SegmentSpec

from .config import DeviceConfig, ModeState
from .drivers.apa102 import StripDriver
from .drivers.button_shim import ButtonShimDriver
from .shaders import KeyframePlayer, ShaderRegistry

logger = logging.getLogger(__name__)

BRIGHTNESS_STEPS = (0.20, 0.40, 0.60, 0.80, 1.00)


class LedEngine:
    """Thread-safe layered LED animation and state controller."""

    def __init__(
        self,
        config: DeviceConfig,
        state: ModeState,
        strip: StripDriver,
        shim: ButtonShimDriver | None = None,
        storage: object | None = None,
        shaders: ShaderRegistry | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self.config = config
        self.state = state
        self.strip = strip
        self.shim = shim
        self.storage = storage
        self.seg_map: SegmentMap = config.segment_map()
        self.shaders = shaders or ShaderRegistry(
            storage.shaders_dir if storage is not None and hasattr(storage, "shaders_dir") else None
        )

        self._start_mono = time.monotonic()
        self._pattern_start_mono = self._start_mono
        self._time_offset_s = 0.0  # wall_epoch - monotonic

        self._stream_pixels: list[RGB] | None = None
        self._stream_brightness: float = 1.0
        self._stream_last_ts: float = 0.0

        self._calibrate_until: float = 0.0
        self._calibrate_start: float = 0.0

        self._pairing_until: float = 0.0
        self._flash_color: RGB | None = None
        self._flash_until: float = 0.0

        self._active_pattern_player: KeyframePlayer | None = None
        self._load_active_pattern_if_needed()

        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

    def _persist_state(self) -> None:
        if self.storage is not None and hasattr(self.storage, "schedule_state_save"):
            self.storage.schedule_state_save(self.state)  # type: ignore[attr-defined]

    def _load_active_pattern_if_needed(self) -> None:
        if not self.state.active_mode.startswith("pattern:"):
            self._active_pattern_player = None
            return
        pat_name = self.state.active_mode.split(":", 1)[1]
        if self.storage is None or not hasattr(self.storage, "load_pattern"):
            self._active_pattern_player = None
            return
        try:
            pattern = self.storage.load_pattern(pat_name)  # type: ignore[attr-defined]
            self._active_pattern_player = KeyframePlayer(pattern, self.seg_map)
            self._pattern_start_mono = time.monotonic()
        except Exception as exc:
            logger.warning("Could not load active pattern %s: %s; falling back to rainbow", pat_name, exc)
            self.state.active_mode = "rainbow"
            self.state.mode_params = {"speed": 0.05, "scale": 1.0, "axis": "u"}
            self._active_pattern_player = None

    def sync_time(self, epoch_ms: int) -> None:
        """Align Pi software wall-clock offset with connected laptop timestamp."""
        with self._lock:
            self._time_offset_s = (epoch_ms / 1000.0) - time.monotonic()

    def wall_epoch(self, mono_now: float | None = None) -> float:
        now = time.monotonic() if mono_now is None else mono_now
        if self._time_offset_s != 0.0:
            return now + self._time_offset_s
        return time.time()

    def flash_shim(self, color: RGB, duration_s: float = 0.25) -> None:
        with self._lock:
            self._flash_color = color
            self._flash_until = time.monotonic() + duration_s

    def set_pairing_indicator(self, active: bool, duration_s: float = 120.0) -> None:
        with self._lock:
            self._pairing_until = (time.monotonic() + duration_s) if active else 0.0

    def set_power(self, on: bool) -> None:
        with self._lock:
            self.state.power = bool(on)
            self._stream_pixels = None
            self.flash_shim((255, 255, 255) if on else (40, 40, 40), 0.18)
            self._persist_state()

    def toggle_power(self) -> bool:
        with self._lock:
            self.set_power(not self.state.power)
            return self.state.power

    def set_brightness(self, level: float) -> None:
        with self._lock:
            self.state.brightness = max(0.0, min(1.0, float(level)))
            self._persist_state()

    def cycle_brightness(self) -> float:
        with self._lock:
            cur = self.state.brightness
            next_step = BRIGHTNESS_STEPS[0]
            for step in BRIGHTNESS_STEPS:
                if step > cur + 0.04:
                    next_step = step
                    break
            self.state.brightness = next_step
            self.flash_shim((220, 220, 255), 0.15)
            self._persist_state()
            return next_step

    def set_busy(self, enabled: bool, params: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.state.busy = bool(enabled)
            if params:
                self.state.busy_params.update(params)
            if enabled and not self.state.power:
                self.state.power = True
            self.flash_shim((255, 30, 0) if enabled else (0, 255, 100), 0.2)
            self._persist_state()

    def toggle_busy(self) -> bool:
        with self._lock:
            self.set_busy(not self.state.busy)
            return self.state.busy

    def set_mode(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> None:
        with self._lock:
            self._stream_pixels = None
            if name.startswith("pattern:"):
                self.state.active_mode = name
                self.state.mode_params = dict(params or {})
                self._load_active_pattern_if_needed()
                if self._active_pattern_player is None:
                    raise FileNotFoundError(f"Pattern not found: {name.split(':', 1)[1]}")
            elif name == "pattern" and params and "name" in params:
                self.state.active_mode = f"pattern:{params['name']}"
                self.state.mode_params = dict(params)
                self._load_active_pattern_if_needed()
                if self._active_pattern_player is None:
                    raise FileNotFoundError(f"Pattern not found: {params['name']}")
            elif name == "static":
                self.state.active_mode = "static"
                if self.state.static_pixels is None:
                    self.state.static_pixels = [[0, 0, 0]] * self.seg_map.total_leds
                self._active_pattern_player = None
            else:
                if self.shaders.get(name) is None:
                    raise ValueError(f"Unknown mode/shader: {name!r}")
                self.state.active_mode = name
                self.state.mode_params = dict(params or {})
                self._active_pattern_player = None

            self.state.power = True
            self.flash_shim((0, 220, 180), 0.16)
            if persist:
                self._persist_state()

    def step_mode_cycle(self, delta: int = 1) -> dict[str, Any]:
        with self._lock:
            cycle = self.config.mode_cycle
            if not cycle:
                return {"mode": self.state.active_mode, "params": self.state.mode_params}
            self.state.cycle_index = (self.state.cycle_index + delta) % len(cycle)
            entry = cycle[self.state.cycle_index]
            self.state.busy = False
            self.set_mode(str(entry["mode"]), dict(entry.get("params", {})), persist=True)
            return entry

    def set_static_leds(
        self,
        updates: list[dict[str, Any]] | None = None,
        all_colors: list[Any] | None = None,
        clear_rest: bool = False,
    ) -> None:
        with self._lock:
            n = self.seg_map.total_leds
            if all_colors is not None:
                pixels = [list(parse_color(c)) for c in all_colors[:n]]
                while len(pixels) < n:
                    pixels.append([0, 0, 0])
            else:
                if clear_rest or not self.state.static_pixels or len(self.state.static_pixels) != n:
                    pixels = [[0, 0, 0] for _ in range(n)]
                else:
                    pixels = [list(px) for px in self.state.static_pixels]
                for item in updates or []:
                    rgb = list(parse_color(item["color"]))
                    if "index" in item:
                        idx = int(item["index"])
                        if 0 <= idx < n:
                            pixels[idx] = rgb
                    elif "indices" in item:
                        for idx in item["indices"]:
                            if 0 <= int(idx) < n:
                                pixels[int(idx)] = rgb
                    elif "zone" in item:
                        for idx in self.seg_map.zone_indices(str(item["zone"])):
                            pixels[idx] = rgb

            self.state.static_pixels = pixels
            self.state.active_mode = "static"
            self.state.busy = False
            self.state.power = True
            self._stream_pixels = None
            self._active_pattern_player = None
            self._persist_state()

    def push_stream_frame(
        self,
        colors: list[RGB],
        brightness: float = 1.0,
        persist_static: bool = False,
        now: float | None = None,
    ) -> None:
        ts = time.monotonic() if now is None else now
        with self._lock:
            n = self.seg_map.total_leds
            padded = list(colors[:n])
            if len(padded) < n:
                padded.extend([(0, 0, 0)] * (n - len(padded)))
            if persist_static:
                self.state.static_pixels = [list(c) for c in padded]
                self.state.active_mode = "static"
                self.state.power = True
                self._stream_pixels = None
                self._persist_state()
            else:
                self._stream_pixels = padded
                self._stream_brightness = max(0.0, min(1.0, float(brightness)))
                self._stream_last_ts = ts
                self.state.power = True

    def start_calibration(self, duration_s: float = 8.0) -> None:
        with self._lock:
            now = time.monotonic()
            self._calibrate_start = now
            self._calibrate_until = now + duration_s

    def update_segments(self, segments: list[SegmentSpec]) -> None:
        with self._lock:
            new_map = SegmentMap(segments)
            self.config.segments = segments
            self.seg_map = new_map
            if hasattr(self.strip, "set_num_leds"):
                self.strip.set_num_leds(new_map.total_leds)  # type: ignore[attr-defined]
            self._load_active_pattern_if_needed()
            if self.storage is not None and hasattr(self.storage, "save_config"):
                self.storage.save_config(self.config)  # type: ignore[attr-defined]

    def update_color_order(self, order: str) -> None:
        with self._lock:
            self.config.color_order = order.upper()
            if hasattr(self.strip, "set_color_order"):
                self.strip.set_color_order(self.config.color_order)  # type: ignore[attr-defined]
            if self.storage is not None and hasattr(self.storage, "save_config"):
                self.storage.save_config(self.config)  # type: ignore[attr-defined]

    def upload_pattern(self, pattern: KeyframePattern, activate: bool = True) -> None:
        with self._lock:
            if self.storage is not None and hasattr(self.storage, "save_pattern"):
                self.storage.save_pattern(pattern)  # type: ignore[attr-defined]
            if activate:
                self.state.active_mode = f"pattern:{pattern.name}"
                self.state.mode_params = {}
                self.state.busy = False
                self.state.power = True
                self._stream_pixels = None
                self._active_pattern_player = KeyframePlayer(pattern, self.seg_map)
                self._pattern_start_mono = time.monotonic()
                self._persist_state()

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            streaming = (
                self._stream_pixels is not None
                and (now - self._stream_last_ts) <= self.config.stream_timeout_s
            )
            return {
                "power": self.state.power,
                "brightness": self.state.brightness,
                "max_brightness_cap": self.config.max_brightness_cap,
                "busy": self.state.busy,
                "active_mode": self.state.active_mode,
                "mode_params": dict(self.state.mode_params),
                "streaming": streaming,
                "total_leds": self.seg_map.total_leds,
                "zones": self.seg_map.zone_names(),
                "color_order": self.config.color_order,
                "available_shaders": self.shaders.names(),
                "wall_epoch": self.wall_epoch(now),
                "pairing": now < self._pairing_until,
            }

    def _render_calibration(self, now: float) -> list[RGB]:
        elapsed = now - self._calibrate_start
        n = self.seg_map.total_leds
        if elapsed < 2.0:
            return [(255, 0, 0)] * n
        if elapsed < 4.0:
            return [(0, 255, 0)] * n
        if elapsed < 6.0:
            return [(0, 0, 255)] * n
        # Final 2s: color-code segments (R, G, B) + directional chase dot
        frame: list[RGB] = [(0, 0, 0)] * n
        zone_colors: list[RGB] = [(255, 0, 0), (0, 255, 0), (0, 80, 255)]
        for z_idx, name in enumerate(self.seg_map.zone_names()):
            col = zone_colors[z_idx % len(zone_colors)]
            indices = self.seg_map.zone_indices(name)
            for pos, led_idx in enumerate(indices):
                scale = 0.25 + 0.75 * (pos / max(1, len(indices) - 1))
                frame[led_idx] = clamp_rgb(col[0] * scale, col[1] * scale, col[2] * scale)
        return frame

    def _update_shim_led(self, pixels: list[RGB], now: float) -> None:
        if self.shim is None:
            return
        bri = self.config.shim_led_brightness
        if now < self._flash_until and self._flash_color is not None:
            self.shim.set_pixel(*self._flash_color, brightness=min(0.6, bri * 1.8))
            return
        if now < self._pairing_until:
            pulse = 0.1 + 0.9 * (0.5 + 0.5 * math.sin(2.0 * math.pi * now * 2.5))
            self.shim.set_pixel(0, 110, 255, brightness=bri * pulse * 1.5)
            return
        if not self.state.power:
            self.shim.set_pixel(0, 0, 0, brightness=0.0)
            return
        if self.state.busy:
            self.shim.set_pixel(255, 20, 0, brightness=bri)
            return
        # Mirror average color of active strip at subtle indicator brightness
        if pixels:
            r = sum(p[0] for p in pixels) // len(pixels)
            g = sum(p[1] for p in pixels) // len(pixels)
            b = sum(p[2] for p in pixels) // len(pixels)
            self.shim.set_pixel(r, g, b, brightness=bri * 0.65)

    def render_tick(self, now: float | None = None) -> list[RGB]:
        """Compute one frame and push it to the strip + SHIM LED."""
        mono = time.monotonic() if now is None else now
        with self._lock:
            n = self.seg_map.total_leds
            brightness = self.state.brightness

            if mono < self._calibrate_until:
                pixels = self._render_calibration(mono)
                brightness = 0.6
            elif not self.state.power:
                pixels = [(0, 0, 0)] * n
                brightness = 0.0
            elif (
                self._stream_pixels is not None
                and (mono - self._stream_last_ts) <= self.config.stream_timeout_s
            ):
                pixels = list(self._stream_pixels)
                brightness = self.state.brightness * self._stream_brightness
            elif self.state.busy:
                pulse_fn = self.shaders.get("pulse")
                assert pulse_fn is not None
                pixels = pulse_fn(mono - self._start_mono, self.seg_map, self.state.busy_params)
            elif self.state.active_mode == "static":
                raw = self.state.static_pixels or []
                pixels = [
                    (int(raw[i][0]), int(raw[i][1]), int(raw[i][2])) if i < len(raw) else (0, 0, 0)
                    for i in range(n)
                ]
            elif self._active_pattern_player is not None:
                pixels = self._active_pattern_player.render(mono - self._pattern_start_mono)
            else:
                shader_fn = self.shaders.get(self.state.active_mode) or self.shaders.get("rainbow")
                assert shader_fn is not None
                params = dict(self.state.mode_params)
                params["_wall_epoch"] = self.wall_epoch(mono)
                pixels = shader_fn(mono - self._start_mono, self.seg_map, params)

            self.strip.show(pixels, brightness=brightness)
            self._update_shim_led(pixels, mono)
            return pixels

    def _run_loop(self) -> None:
        period = 1.0 / self.config.fps
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            try:
                self.render_tick(t0)
            except Exception:
                logger.exception("Error in LED render tick")
            elapsed = time.monotonic() - t0
            sleep_for = max(0.002, period - elapsed)
            self._stop_evt.wait(sleep_for)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run_loop, name="led-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        if self.storage is not None and hasattr(self.storage, "flush_pending_state"):
            self.storage.flush_pending_state()  # type: ignore[attr-defined]
        self.strip.clear()
        if self.shim is not None:
            self.shim.set_pixel(0, 0, 0, 0.0)
