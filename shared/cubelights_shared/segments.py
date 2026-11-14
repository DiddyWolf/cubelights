"""Logical-to-physical LED segment mapping for series-wired LED strips."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class SegmentSpec:
    """Configuration for a single physical LED strip segment in the series chain."""

    name: str
    length: int
    reversed: bool = False
    start_xy: tuple[float, float] = (0.0, 0.0)
    end_xy: tuple[float, float] = (1.0, 0.0)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Segment name must be non-empty")
        if self.length <= 0:
            raise ValueError(f"Segment {self.name!r} length must be > 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "length": self.length,
            "reversed": self.reversed,
            "start_xy": [self.start_xy[0], self.start_xy[1]],
            "end_xy": [self.end_xy[0], self.end_xy[1]],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SegmentSpec:
        s_xy = data.get("start_xy", [0.0, 0.0])
        e_xy = data.get("end_xy", [1.0, 0.0])
        return cls(
            name=str(data["name"]),
            length=int(data["length"]),
            reversed=bool(data.get("reversed", False)),
            start_xy=(float(s_xy[0]), float(s_xy[1])),
            end_xy=(float(e_xy[0]), float(e_xy[1])),
        )


@dataclass(slots=True, frozen=True)
class LedPoint:
    """Physical and logical coordinate metadata for a single LED."""

    index: int
    segment_name: str
    segment_index: int
    local_index: int
    u: float
    x: float
    y: float


class SegmentMap:
    """Maps 1D/2D logical coordinates and named zones to physical strip indices."""

    def __init__(self, segments: list[SegmentSpec]) -> None:
        if not segments:
            raise ValueError("SegmentMap requires at least one segment")
        names = [s.name for s in segments]
        if len(set(names)) != len(names):
            raise ValueError("Segment names must be unique")

        self.segments = list(segments)
        self.total_leds = sum(s.length for s in self.segments)
        self._zones: dict[str, list[int]] = {}
        self.points: list[LedPoint] = []
        self._build()

    def _build(self) -> None:
        phys_idx = 0
        denom_global = max(1, self.total_leds - 1)

        for seg_idx, seg in enumerate(self.segments):
            zone_indices: list[int] = []
            seg_denom = max(1, seg.length - 1)
            for offset in range(seg.length):
                local_pos = (seg.length - 1 - offset) if seg.reversed else offset
                t_local = local_pos / seg_denom if seg.length > 1 else 0.5
                x = seg.start_xy[0] + (seg.end_xy[0] - seg.start_xy[0]) * t_local
                y = seg.start_xy[1] + (seg.end_xy[1] - seg.start_xy[1]) * t_local
                logical_global = (phys_idx - offset) + local_pos
                u = logical_global / denom_global
                self.points.append(
                    LedPoint(
                        index=phys_idx,
                        segment_name=seg.name,
                        segment_index=seg_idx,
                        local_index=local_pos,
                        u=u,
                        x=x,
                        y=y,
                    )
                )
                zone_indices.append(phys_idx)
                phys_idx += 1

            # Store zone indices in logical order so gradient/chase along a zone respects reversed
            if seg.reversed:
                zone_indices.reverse()
            self._zones[seg.name] = zone_indices

    def zone_names(self) -> list[str]:
        """Return configured segment/zone names in physical chain order."""
        return [s.name for s in self.segments]

    def zone_indices(self, name: str) -> list[int]:
        """Return physical LED indices belonging to a named segment in logical order."""
        if name not in self._zones:
            raise KeyError(f"Unknown segment/zone: {name!r}")
        return list(self._zones[name])

    def to_dict(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.segments]

    @classmethod
    def from_dict(cls, items: list[dict[str, Any]]) -> SegmentMap:
        return cls([SegmentSpec.from_dict(item) for item in items])


def default_segment_map() -> SegmentMap:
    """Default 6 + 13 + 10 (29 LED) series-wired segment layout."""
    return SegmentMap(
        [
            SegmentSpec(
                name="strip_1",
                length=6,
                reversed=False,
                start_xy=(0.0, 0.25),
                end_xy=(0.35, 0.25),
            ),
            SegmentSpec(
                name="strip_2",
                length=13,
                reversed=False,
                start_xy=(0.0, 0.65),
                end_xy=(1.0, 0.65),
            ),
            SegmentSpec(
                name="strip_3",
                length=10,
                reversed=False,
                start_xy=(0.15, 1.0),
                end_xy=(0.85, 1.0),
            ),
        ]
    )
