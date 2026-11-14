"""Crash-safe /data storage manager with atomic writes and ro/rw remounting."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from cubelights_shared.patterns import KeyframePattern

from .config import DeviceConfig, ModeState

logger = logging.getLogger(__name__)
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def sanitize_pattern_name(name: str) -> str:
    """Validate and return safe pattern identifier."""
    cleaned = name.strip()
    if not SAFE_NAME_RE.match(cleaned):
        raise ValueError(
            f"Invalid pattern name {name!r}; must match [A-Za-z0-9_-]{{1,64}}"
        )
    return cleaned


class StorageManager:
    """Manages config, persisted state, declarative patterns, and custom shaders under data_dir."""

    def __init__(
        self,
        data_dir: str | Path = "/data",
        remount_ro: bool = False,
        debounce_s: float = 3.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.remount_ro = remount_ro
        self.debounce_s = debounce_s
        self._lock = threading.RLock()
        self._pending_state: ModeState | None = None
        self._save_timer: threading.Timer | None = None

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def patterns_dir(self) -> Path:
        return self.data_dir / "patterns"

    @property
    def shaders_dir(self) -> Path:
        return self.data_dir / "shaders"

    def ensure_layout(self) -> None:
        """Create expected subdirectories if not already present."""
        with self._writable_mount():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.patterns_dir.mkdir(parents=True, exist_ok=True)
            self.shaders_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _writable_mount(self) -> Iterator[None]:
        """Temporarily remount data_dir rw when running with read-only /data protection."""
        if not self.remount_ro or not self.data_dir.is_mount():
            yield
            return
        target = str(self.data_dir)
        prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
        subprocess.run([*prefix, "/bin/mount", "-o", "remount,rw", target], check=True)
        try:
            yield
        finally:
            subprocess.run(["/bin/sync"], check=False)
            res = subprocess.run(
                [*prefix, "/bin/mount", "-o", "remount,ro", target],
                check=False,
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                logger.warning("Failed to remount %s read-only: %s", target, res.stderr.strip())

    def _atomic_write_json(self, path: Path, payload: object) -> None:
        with self._lock:
            with self._writable_mount():
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = path.with_suffix(path.suffix + ".tmp")
                serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
                with open(tmp_path, "w", encoding="utf-8") as fh:
                    fh.write(serialized)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_path, path)

    def load_config(self) -> DeviceConfig:
        """Load DeviceConfig or create default config.json if missing."""
        with self._lock:
            if not self.config_path.exists():
                cfg = DeviceConfig()
                try:
                    self.save_config(cfg)
                except OSError as exc:
                    logger.warning("Could not persist default config.json: %s", exc)
                return cfg
            with open(self.config_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            return DeviceConfig.from_dict(raw)

    def save_config(self, config: DeviceConfig) -> None:
        self._atomic_write_json(self.config_path, config.to_dict())

    def load_state(self) -> ModeState:
        """Load ModeState or return defaults if state.json does not exist."""
        with self._lock:
            if not self.state_path.exists():
                return ModeState()
            try:
                with open(self.state_path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                return ModeState.from_dict(raw)
            except (OSError, ValueError, KeyError) as exc:
                logger.warning("Corrupt/unreadable state.json, using defaults: %s", exc)
                return ModeState()

    def save_state(self, state: ModeState) -> None:
        """Immediately persist ModeState atomically."""
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            self._pending_state = None
            self._atomic_write_json(self.state_path, state.to_dict())

    def schedule_state_save(self, state: ModeState) -> None:
        """Debounce state persistence to minimize SD flash writes."""
        with self._lock:
            self._pending_state = ModeState.from_dict(state.to_dict())
            if self._save_timer is not None:
                self._save_timer.cancel()
            if self.debounce_s <= 0:
                self.save_state(self._pending_state)
                return
            timer = threading.Timer(self.debounce_s, self.flush_pending_state)
            timer.daemon = True
            self._save_timer = timer
            timer.start()

    def flush_pending_state(self) -> None:
        """Flush any debounced ModeState immediately (e.g., on shutdown)."""
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            if self._pending_state is not None:
                pending = self._pending_state
                self._pending_state = None
                self._atomic_write_json(self.state_path, pending.to_dict())

    def save_pattern(self, pattern: KeyframePattern) -> Path:
        name = sanitize_pattern_name(pattern.name)
        target = self.patterns_dir / f"{name}.json"
        self._atomic_write_json(target, pattern.to_dict())
        return target

    def load_pattern(self, name: str) -> KeyframePattern:
        safe = sanitize_pattern_name(name)
        target = self.patterns_dir / f"{safe}.json"
        if not target.exists():
            raise FileNotFoundError(f"Pattern not found: {safe}")
        with open(target, "r", encoding="utf-8") as fh:
            return KeyframePattern.from_dict(json.load(fh))

    def delete_pattern(self, name: str) -> bool:
        safe = sanitize_pattern_name(name)
        target = self.patterns_dir / f"{safe}.json"
        with self._lock:
            if not target.exists():
                return False
            with self._writable_mount():
                target.unlink()
            return True

    def list_patterns(self) -> list[dict[str, str | int]]:
        """List stored declarative patterns with metadata."""
        if not self.patterns_dir.exists():
            return []
        results: list[dict[str, str | int]] = []
        for path in sorted(self.patterns_dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                results.append(
                    {
                        "name": str(data.get("name", path.stem)),
                        "description": str(data.get("description", "")),
                        "keyframes": len(data.get("keyframes", [])),
                    }
                )
            except (OSError, ValueError) as exc:
                logger.warning("Skipping invalid pattern file %s: %s", path, exc)
        return results
