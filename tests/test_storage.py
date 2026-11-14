"""Tests for StorageManager atomic writes, pattern CRUD, and debounced saves."""

import time

from cubelights_shared.patterns import Keyframe, KeyframePattern

from cubelights_pi.config import DeviceConfig, ModeState
from cubelights_pi.storage import StorageManager


def test_storage_config_state_and_pattern_crud(tmp_path):
    storage = StorageManager(data_dir=tmp_path, remount_ro=False, debounce_s=0.05)
    storage.ensure_layout()

    cfg = storage.load_config()
    assert cfg.color_order == "BGR"
    cfg.max_brightness_cap = 0.6
    storage.save_config(cfg)
    assert storage.load_config().max_brightness_cap == 0.6

    st = ModeState(active_mode="wave", brightness=0.45, busy=True)
    storage.schedule_state_save(st)
    time.sleep(0.15)
    loaded = storage.load_state()
    assert loaded.active_mode == "wave"
    assert loaded.busy is True
    assert loaded.brightness == 0.45

    pat = KeyframePattern(name="test_pat", description="desc", keyframes=[Keyframe(500, fill=(10, 20, 30))])
    storage.save_pattern(pat)
    listed = storage.list_patterns()
    assert [p["name"] for p in listed] == ["test_pat"]
    assert storage.load_pattern("test_pat").keyframes[0].fill == (10, 20, 30)
    assert storage.delete_pattern("test_pat") is True
    assert storage.list_patterns() == []
