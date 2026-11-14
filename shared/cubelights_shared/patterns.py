"""Declarative keyframe animation schema and color utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EasingMode = Literal["linear", "ease_in_out", "step"]
RGB = tuple[int, int, int]


def clamp_rgb(r: int | float, g: int | float, b: int | float) -> RGB:
    """Clamp RGB values to 0..255 integer tuple."""
    return (
        max(0, min(255, int(round(r)))),
        max(0, min(255, int(round(g)))),
        max(0, min(255, int(round(b)))),
    )


def parse_color(value: Any) -> RGB:
    """Parse RGB from '#RRGGBB' hex string or [r, g, b] sequence."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("#"):
            text = text[1:]
        if len(text) != 6:
            raise ValueError(f"Invalid hex color: {value!r}")
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return clamp_rgb(value[0], value[1], value[2])
    raise ValueError(f"Unsupported color format: {value!r}")


def ease_value(t: float, mode: EasingMode) -> float:
    """Apply interpolation easing to t in [0.0, 1.0]."""
    t = max(0.0, min(1.0, t))
    if mode == "step":
        return 0.0 if t < 1.0 else 1.0
    if mode == "ease_in_out":
        return t * t * (3.0 - 2.0 * t)
    return t


@dataclass(slots=True, frozen=True)
class KeyframeTarget:
    """Color assignment target inside a keyframe (indices, range, or named zone)."""

    color: RGB
    indices: list[int] | None = None
    zone: str | None = None
    start: int | None = None
    end: int | None = None

    def resolve_indices(self, total_leds: int, zone_lookup: dict[str, list[int]]) -> list[int]:
        """Resolve target specification to valid physical LED indices."""
        if self.indices is not None:
            return [i for i in self.indices if 0 <= i < total_leds]
        if self.zone is not None:
            if self.zone not in zone_lookup:
                raise KeyError(f"Unknown zone in pattern target: {self.zone!r}")
            return list(zone_lookup[self.zone])
        if self.start is not None:
            end = self.end if self.end is not None else self.start
            lo = max(0, min(self.start, end))
            hi = min(total_leds - 1, max(self.start, end))
            return list(range(lo, hi + 1))
        return list(range(total_leds))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"color": list(self.color)}
        if self.indices is not None:
            out["indices"] = list(self.indices)
        if self.zone is not None:
            out["zone"] = self.zone
        if self.start is not None:
            out["start"] = self.start
        if self.end is not None:
            out["end"] = self.end
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeyframeTarget:
        return cls(
            color=parse_color(data["color"]),
            indices=[int(i) for i in data["indices"]] if "indices" in data else None,
            zone=str(data["zone"]) if "zone" in data else None,
            start=int(data["start"]) if "start" in data else None,
            end=int(data["end"]) if "end" in data else None,
        )


@dataclass(slots=True, frozen=True)
class Keyframe:
    """Single keyframe state with transition duration and easing."""

    duration_ms: int
    easing: EasingMode = "linear"
    fill: RGB | None = None
    targets: list[KeyframeTarget] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.duration_ms <= 0:
            raise ValueError("Keyframe duration_ms must be > 0")
        if self.easing not in ("linear", "ease_in_out", "step"):
            raise ValueError(f"Unsupported easing mode: {self.easing!r}")

    def render(self, total_leds: int, zone_lookup: dict[str, list[int]]) -> list[RGB]:
        """Materialize this keyframe into a full per-LED RGB frame."""
        base: RGB = self.fill if self.fill is not None else (0, 0, 0)
        pixels: list[RGB] = [base] * total_leds
        for target in self.targets:
            for idx in target.resolve_indices(total_leds, zone_lookup):
                pixels[idx] = target.color
        return pixels

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "duration_ms": self.duration_ms,
            "easing": self.easing,
            "targets": [t.to_dict() for t in self.targets],
        }
        if self.fill is not None:
            out["fill"] = list(self.fill)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Keyframe:
        fill = parse_color(data["fill"]) if data.get("fill") is not None else None
        targets = [KeyframeTarget.from_dict(t) for t in data.get("targets", [])]
        return cls(
            duration_ms=int(data["duration_ms"]),
            easing=data.get("easing", "linear"),
            fill=fill,
            targets=targets,
        )


@dataclass(slots=True, frozen=True)
class KeyframePattern:
    """Complete declarative animation stored on /data/patterns/."""

    name: str
    keyframes: list[Keyframe]
    description: str = ""
    loop: int = 0  # 0 = loop forever

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Pattern name must be non-empty")
        if not self.keyframes:
            raise ValueError("KeyframePattern requires at least one keyframe")
        if self.loop < 0:
            raise ValueError("loop must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "loop": self.loop,
            "keyframes": [kf.to_dict() for kf in self.keyframes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeyframePattern:
        return cls(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            loop=int(data.get("loop", 0)),
            keyframes=[Keyframe.from_dict(kf) for kf in data["keyframes"]],
        )
