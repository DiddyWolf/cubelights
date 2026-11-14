"""Tests for SegmentMap (6 + 13 + 10) and KeyframePattern schema/evaluation."""

import json
from pathlib import Path

from cubelights_shared.patterns import KeyframePattern
from cubelights_shared.segments import SegmentMap, SegmentSpec, default_segment_map


def test_default_segment_map_is_6_13_10():
    seg_map = default_segment_map()
    assert seg_map.total_leds == 29
    assert seg_map.zone_names() == ["strip_1", "strip_2", "strip_3"]
    assert len(seg_map.zone_indices("strip_1")) == 6
    assert len(seg_map.zone_indices("strip_2")) == 13
    assert len(seg_map.zone_indices("strip_3")) == 10
    assert seg_map.points[0].u == 0.0
    assert seg_map.points[-1].u == 1.0


def test_reversed_segment_mapping_and_zone_order():
    seg_map = SegmentMap(
        [
            SegmentSpec("a", 4, reversed=False, start_xy=(0.0, 0.0), end_xy=(1.0, 0.0)),
            SegmentSpec("b", 4, reversed=True, start_xy=(0.0, 1.0), end_xy=(1.0, 1.0)),
        ]
    )
    assert seg_map.zone_indices("a") == [0, 1, 2, 3]
    # Physical indices 4..7 correspond to logical positions 3..0 when reversed=True
    assert seg_map.zone_indices("b") == [7, 6, 5, 4]
    assert seg_map.points[4].x == 1.0
    assert seg_map.points[7].x == 0.0


def test_example_pattern_files_load_and_validate():
    patterns_dir = Path(__file__).resolve().parent.parent / "patterns"
    files = list(patterns_dir.glob("*.json"))
    assert len(files) >= 4
    seg_map = default_segment_map()
    zone_lookup = {z: seg_map.zone_indices(z) for z in seg_map.zone_names()}

    for file in files:
        raw = json.loads(file.read_text(encoding="utf-8"))
        pat = KeyframePattern.from_dict(raw)
        assert pat.to_dict()["name"] == pat.name
        for kf in pat.keyframes:
            frame = kf.render(seg_map.total_leds, zone_lookup)
            assert len(frame) == 29
