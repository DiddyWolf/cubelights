"""Built-in LED shaders, seasonal palettes, keyframe player, and custom shader loader."""

from __future__ import annotations

import colorsys
import datetime
import importlib.util
import logging
import math
from pathlib import Path
from typing import Any, Callable

from cubelights_shared.patterns import KeyframePattern, RGB, clamp_rgb, ease_value, parse_color
from cubelights_shared.segments import LedPoint, SegmentMap

logger = logging.getLogger(__name__)

ShaderFn = Callable[[float, SegmentMap, dict[str, Any]], list[RGB]]

SEASONAL_PALETTES: dict[str, list[RGB]] = {
    "spring": [(144, 238, 144), (255, 182, 193), (255, 250, 170), (135, 206, 250)],
    "summer": [(255, 140, 0), (255, 215, 0), (0, 200, 255), (0, 230, 160)],
    "fall": [(255, 80, 0), (210, 45, 0), (255, 165, 0), (140, 55, 10)],
    "halloween": [(255, 85, 0), (140, 0, 255), (255, 45, 0), (60, 0, 120)],
    "winter": [(180, 230, 255), (60, 140, 255), (235, 248, 255), (90, 180, 240)],
    "holiday": [(220, 15, 15), (15, 200, 35), (255, 210, 120), (220, 15, 15)],
    "pride": [
        (228, 3, 3),
        (255, 140, 0),
        (255, 237, 0),
        (0, 128, 38),
        (36, 64, 142),
        (115, 41, 130),
    ],
}


def _coord_for_point(pt: LedPoint, axis: str) -> float:
    if axis == "x":
        return pt.x
    if axis == "y":
        return pt.y
    if axis == "2d":
        return 0.65 * pt.x + 0.35 * pt.y
    return pt.u


def _lerp_rgb(c1: RGB, c2: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return clamp_rgb(
        c1[0] + (c2[0] - c1[0]) * t,
        c1[1] + (c2[1] - c1[1]) * t,
        c1[2] + (c2[2] - c1[2]) * t,
    )


def _sample_palette(palette: list[RGB], pos: float) -> RGB:
    if not palette:
        return (0, 0, 0)
    if len(palette) == 1:
        return palette[0]
    wrapped = pos % 1.0
    scaled = wrapped * len(palette)
    idx0 = int(math.floor(scaled)) % len(palette)
    idx1 = (idx0 + 1) % len(palette)
    frac = scaled - math.floor(scaled)
    return _lerp_rgb(palette[idx0], palette[idx1], frac)


def resolve_seasonal_theme(theme: str, wall_now: datetime.datetime) -> str:
    """Resolve 'auto' seasonal theme using the synced wall-clock calendar date."""
    cleaned = theme.lower().strip()
    if cleaned != "auto":
        return cleaned if cleaned in SEASONAL_PALETTES else "winter"
    month, day = wall_now.month, wall_now.day
    if month == 10:
        return "halloween"
    if month == 12 and day <= 26:
        return "holiday"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 11):
        return "fall"
    return "winter"


def shader_rainbow(t: float, seg_map: SegmentMap, params: dict[str, Any]) -> list[RGB]:
    speed = float(params.get("speed", 0.05))
    scale = float(params.get("scale", 1.0))
    saturation = max(0.0, min(1.0, float(params.get("saturation", 1.0))))
    axis = str(params.get("axis", "u"))
    direction = -1.0 if params.get("reverse", False) else 1.0

    pixels: list[RGB] = []
    for pt in seg_map.points:
        coord = _coord_for_point(pt, axis)
        hue = (coord * scale - direction * t * speed) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, saturation, 1.0)
        pixels.append((int(r * 255), int(g * 255), int(b * 255)))
    return pixels


def shader_pulse(t: float, seg_map: SegmentMap, params: dict[str, Any]) -> list[RGB]:
    color = parse_color(params.get("color", "#FF1800"))
    speed = float(params.get("speed", 0.35))
    min_level = max(0.0, min(1.0, float(params.get("min_level", 0.08))))
    max_level = max(min_level, min(1.0, float(params.get("max_level", 1.0))))
    phase = 0.5 - 0.5 * math.cos(2.0 * math.pi * t * speed)
    level = min_level + (max_level - min_level) * phase
    px = clamp_rgb(color[0] * level, color[1] * level, color[2] * level)
    return [px] * seg_map.total_leds


def shader_wave(t: float, seg_map: SegmentMap, params: dict[str, Any]) -> list[RGB]:
    raw_colors = params.get("colors", ["#002B7F", "#0088FF", "#00E5FF"])
    palette = [parse_color(c) for c in raw_colors] or [(0, 64, 255), (0, 220, 255)]
    speed = float(params.get("speed", 0.18))
    scale = float(params.get("scale", 1.4))
    axis = str(params.get("axis", "u"))

    pixels: list[RGB] = []
    for pt in seg_map.points:
        c = _coord_for_point(pt, axis)
        w1 = 0.5 + 0.5 * math.sin(2.0 * math.pi * (c * scale - t * speed))
        w2 = 0.5 + 0.5 * math.sin(2.0 * math.pi * (c * scale * 0.5 + t * speed * 0.6))
        pos = (0.65 * w1 + 0.35 * w2) * (len(palette) - 1) / len(palette)
        color = _sample_palette(palette, pos)
        intensity = 0.45 + 0.55 * w1
        pixels.append(clamp_rgb(color[0] * intensity, color[1] * intensity, color[2] * intensity))
    return pixels


def shader_seasonal(t: float, seg_map: SegmentMap, params: dict[str, Any]) -> list[RGB]:
    theme_raw = str(params.get("theme", "auto"))
    wall_epoch = float(params.get("_wall_epoch", t))
    wall_dt = datetime.datetime.fromtimestamp(wall_epoch)
    theme = resolve_seasonal_theme(theme_raw, wall_dt)
    palette = SEASONAL_PALETTES.get(theme, SEASONAL_PALETTES["winter"])
    speed = float(params.get("speed", 0.08))
    scale = float(params.get("scale", 1.0))
    axis = str(params.get("axis", "u"))

    pixels: list[RGB] = []
    for pt in seg_map.points:
        c = _coord_for_point(pt, axis)
        pos = c * scale * 0.75 + t * speed
        base = _sample_palette(palette, pos)
        shimmer = 0.78 + 0.22 * math.sin(2.0 * math.pi * (t * speed * 2.1 + pt.index * 0.17))
        pixels.append(clamp_rgb(base[0] * shimmer, base[1] * shimmer, base[2] * shimmer))
    return pixels


def shader_warm_desk(t: float, seg_map: SegmentMap, params: dict[str, Any]) -> list[RGB]:
    warmth = max(0.0, min(1.0, float(params.get("warmth", 0.85))))
    speed = float(params.get("speed", 0.04))
    cool_white: RGB = (255, 240, 215)
    warm_amber: RGB = (255, 168, 68)
    base = _lerp_rgb(cool_white, warm_amber, warmth)

    pixels: list[RGB] = []
    for pt in seg_map.points:
        drift = 0.90 + 0.10 * math.sin(2.0 * math.pi * (t * speed + pt.u * 0.5))
        pixels.append(clamp_rgb(base[0] * drift, base[1] * drift, base[2] * drift))
    return pixels


def shader_sparkle(t: float, seg_map: SegmentMap, params: dict[str, Any]) -> list[RGB]:
    base = parse_color(params.get("base", "#0A1833"))
    spark = parse_color(params.get("spark", "#88CCFF"))
    speed = float(params.get("speed", 1.1))
    density = max(0.05, min(0.9, float(params.get("density", 0.2))))

    pixels: list[RGB] = []
    for pt in seg_map.points:
        # Smooth pseudo-random phase per LED without harsh strobe
        phase_offset = ((pt.index * 37 + 13) % 29) / 29.0
        freq = 0.6 + ((pt.index * 19) % 7) * 0.17
        wave = math.sin(2.0 * math.pi * (t * speed * freq + phase_offset))
        threshold = 1.0 - 2.0 * density
        if wave > threshold:
            amt = ((wave - threshold) / (1.0 - threshold)) ** 2
            pixels.append(_lerp_rgb(base, spark, amt))
        else:
            pixels.append(base)
    return pixels


def shader_chase(t: float, seg_map: SegmentMap, params: dict[str, Any]) -> list[RGB]:
    head_color = parse_color(params.get("color", "#00FFAA"))
    bg_color = parse_color(params.get("bg", "#040812"))
    speed = float(params.get("speed", 0.35))
    tail = max(0.05, min(0.8, float(params.get("tail", 0.25))))
    head_u = (t * speed) % 1.0

    pixels: list[RGB] = []
    for pt in seg_map.points:
        dist = (head_u - pt.u) % 1.0
        if dist < tail:
            amt = (1.0 - dist / tail) ** 2
            pixels.append(_lerp_rgb(bg_color, head_color, amt))
        else:
            pixels.append(bg_color)
    return pixels


def shader_gradient(t: float, seg_map: SegmentMap, params: dict[str, Any]) -> list[RGB]:
    raw_colors = params.get("colors", ["#FF4E50", "#FC913A", "#F9D423"])
    palette = [parse_color(c) for c in raw_colors] or [(255, 80, 0), (0, 120, 255)]
    speed = float(params.get("speed", 0.0))
    axis = str(params.get("axis", "u"))

    pixels: list[RGB] = []
    for pt in seg_map.points:
        c = _coord_for_point(pt, axis)
        if speed == 0.0 and len(palette) >= 2:
            scaled = max(0.0, min(1.0, c)) * (len(palette) - 1)
            i0 = min(len(palette) - 2, int(math.floor(scaled)))
            pixels.append(_lerp_rgb(palette[i0], palette[i0 + 1], scaled - i0))
        else:
            pixels.append(_sample_palette(palette, c + t * speed))
    return pixels


def shader_solid(t: float, seg_map: SegmentMap, params: dict[str, Any]) -> list[RGB]:
    del t
    default_color = parse_color(params.get("color", "#4488FF"))
    pixels: list[RGB] = [default_color] * seg_map.total_leds
    zones = params.get("zones")
    if isinstance(zones, dict):
        for zone_name, col in zones.items():
            try:
                rgb = parse_color(col)
                for idx in seg_map.zone_indices(str(zone_name)):
                    pixels[idx] = rgb
            except (KeyError, ValueError):
                continue
    return pixels


class KeyframePlayer:
    """Evaluates an uploaded KeyframePattern deterministically at elapsed time t."""

    def __init__(self, pattern: KeyframePattern, seg_map: SegmentMap) -> None:
        self.pattern = pattern
        self.seg_map = seg_map
        self._zone_lookup = {name: seg_map.zone_indices(name) for name in seg_map.zone_names()}
        self._frames = [kf.render(seg_map.total_leds, self._zone_lookup) for kf in pattern.keyframes]
        self._durations = [kf.duration_ms / 1000.0 for kf in pattern.keyframes]
        self._total_duration = max(0.001, sum(self._durations))

    def render(self, elapsed_s: float) -> list[RGB]:
        if len(self._frames) == 1:
            return list(self._frames[0])

        loops_done = int(elapsed_s // self._total_duration)
        if self.pattern.loop > 0 and loops_done >= self.pattern.loop:
            return list(self._frames[-1])

        pos = elapsed_s % self._total_duration
        acc = 0.0
        for idx, dur in enumerate(self._durations):
            if pos <= acc + dur or idx == len(self._durations) - 1:
                next_idx = (idx + 1) % len(self._frames)
                if self.pattern.loop > 0 and idx == len(self._durations) - 1 and loops_done + 1 >= self.pattern.loop:
                    next_idx = idx
                local_t = (pos - acc) / dur if dur > 0 else 1.0
                eased = ease_value(local_t, self.pattern.keyframes[idx].easing)
                f0 = self._frames[idx]
                f1 = self._frames[next_idx]
                return [_lerp_rgb(f0[i], f1[i], eased) for i in range(self.seg_map.total_leds)]
            acc += dur
        return list(self._frames[-1])


class ShaderRegistry:
    """Registry of built-in and optional user-provided /data/shaders/*.py plugins."""

    def __init__(self, custom_shaders_dir: Path | None = None) -> None:
        self._shaders: dict[str, ShaderFn] = {
            "rainbow": shader_rainbow,
            "pulse": shader_pulse,
            "wave": shader_wave,
            "seasonal": shader_seasonal,
            "warm_desk": shader_warm_desk,
            "sparkle": shader_sparkle,
            "chase": shader_chase,
            "gradient": shader_gradient,
            "solid": shader_solid,
        }
        if custom_shaders_dir is not None:
            self.load_custom_dir(custom_shaders_dir)

    def names(self) -> list[str]:
        return sorted(self._shaders.keys())

    def get(self, name: str) -> ShaderFn | None:
        return self._shaders.get(name)

    def load_custom_dir(self, directory: Path) -> int:
        """Load optional Python shaders from /data/shaders/*.py."""
        if not directory.exists():
            return 0
        loaded = 0
        for py_file in sorted(directory.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location(f"cubelights_custom_{py_file.stem}", py_file)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                shader_name = str(getattr(mod, "NAME", py_file.stem))
                render_fn = getattr(mod, "render", None)
                if callable(render_fn):
                    self._shaders[shader_name] = render_fn
                    loaded += 1
            except Exception as exc:
                logger.warning("Failed to load custom shader %s: %s", py_file, exc)
        return loaded
