"""Configuration and persistent runtime state models for cubelights-pi."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cubelights_shared.segments import SegmentMap, SegmentSpec, default_segment_map

VALID_COLOR_ORDERS = {"RGB", "RBG", "GRB", "GBR", "BRG", "BGR"}
VALID_LED_PROTOCOLS = {"apa102", "sk9822"}


def default_mode_cycle() -> list[dict[str, Any]]:
    """Curated list of modes cycled via Button SHIM A/D."""
    return [
        {"mode": "rainbow", "params": {"speed": 0.05, "scale": 1.0, "axis": "u"}},
        {"mode": "wave", "params": {"speed": 0.18, "colors": ["#002B7F", "#0088FF", "#00E5FF"]}},
        {"mode": "seasonal", "params": {"theme": "auto", "speed": 0.08}},
        {"mode": "warm_desk", "params": {"speed": 0.04, "warmth": 0.85}},
        {"mode": "sparkle", "params": {"base": "#0A1833", "spark": "#88CCFF", "density": 0.15}},
        {"mode": "chase", "params": {"color": "#00FFAA", "bg": "#040812", "speed": 0.35}},
        {"mode": "seasonal", "params": {"theme": "holiday", "speed": 0.1}},
        {"mode": "seasonal", "params": {"theme": "halloween", "speed": 0.12}},
    ]


def default_button_bindings() -> dict[str, str]:
    """Default Button SHIM action bindings."""
    return {
        "A_short": "next_mode",
        "B_short": "toggle_busy",
        "C_short": "cycle_brightness",
        "D_short": "prev_mode",
        "E_short": "toggle_power",
        "E_long": "bt_pairing",
        "AE_long": "safe_shutdown",
    }


@dataclass(slots=True)
class DeviceConfig:
    """Hardware and engine configuration stored in /data/config.json."""

    color_order: str = "BGR"
    led_protocol: str = "apa102"
    spi_bus: int = 0
    spi_device: int = 0
    spi_speed_hz: int = 4_000_000
    max_brightness_cap: float = 0.75
    fps: float = 45.0
    stream_timeout_s: float = 5.0
    shim_led_brightness: float = 0.11
    segments: list[SegmentSpec] = field(default_factory=lambda: default_segment_map().segments)
    button_bindings: dict[str, str] = field(default_factory=default_button_bindings)
    mode_cycle: list[dict[str, Any]] = field(default_factory=default_mode_cycle)

    def __post_init__(self) -> None:
        self.color_order = self.color_order.upper()
        if self.color_order not in VALID_COLOR_ORDERS:
            raise ValueError(f"Invalid color_order: {self.color_order!r}")
        self.led_protocol = self.led_protocol.lower()
        if self.led_protocol not in VALID_LED_PROTOCOLS:
            raise ValueError(f"Invalid led_protocol: {self.led_protocol!r}")
        self.max_brightness_cap = max(0.05, min(1.0, float(self.max_brightness_cap)))
        self.fps = max(5.0, min(120.0, float(self.fps)))
        self.stream_timeout_s = max(0.5, float(self.stream_timeout_s))

    def segment_map(self) -> SegmentMap:
        return SegmentMap(self.segments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "color_order": self.color_order,
            "led_protocol": self.led_protocol,
            "spi_bus": self.spi_bus,
            "spi_device": self.spi_device,
            "spi_speed_hz": self.spi_speed_hz,
            "max_brightness_cap": self.max_brightness_cap,
            "fps": self.fps,
            "stream_timeout_s": self.stream_timeout_s,
            "shim_led_brightness": self.shim_led_brightness,
            "segments": [s.to_dict() for s in self.segments],
            "button_bindings": dict(self.button_bindings),
            "mode_cycle": list(self.mode_cycle),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceConfig:
        raw_segs = data.get("segments")
        segments = (
            [SegmentSpec.from_dict(s) for s in raw_segs]
            if raw_segs
            else default_segment_map().segments
        )
        bindings = default_button_bindings()
        if isinstance(data.get("button_bindings"), dict):
            bindings.update({str(k): str(v) for k, v in data["button_bindings"].items()})

        mode_cycle = data.get("mode_cycle") or default_mode_cycle()
        return cls(
            color_order=str(data.get("color_order", "BGR")),
            led_protocol=str(data.get("led_protocol", "apa102")),
            spi_bus=int(data.get("spi_bus", 0)),
            spi_device=int(data.get("spi_device", 0)),
            spi_speed_hz=int(data.get("spi_speed_hz", 4_000_000)),
            max_brightness_cap=float(data.get("max_brightness_cap", 0.75)),
            fps=float(data.get("fps", 45.0)),
            stream_timeout_s=float(data.get("stream_timeout_s", 5.0)),
            shim_led_brightness=float(data.get("shim_led_brightness", 0.11)),
            segments=segments,
            button_bindings=bindings,
            mode_cycle=list(mode_cycle),
        )


@dataclass(slots=True)
class ModeState:
    """Persisted runtime LED state stored in /data/state.json."""

    power: bool = True
    brightness: float = 0.65
    busy: bool = False
    busy_params: dict[str, Any] = field(
        default_factory=lambda: {"color": "#FF1800", "speed": 0.35, "min_level": 0.08}
    )
    active_mode: str = "rainbow"
    mode_params: dict[str, Any] = field(
        default_factory=lambda: {"speed": 0.05, "scale": 1.0, "axis": "u"}
    )
    cycle_index: int = 0
    static_pixels: list[list[int]] | None = None

    def __post_init__(self) -> None:
        self.brightness = max(0.0, min(1.0, float(self.brightness)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "power": self.power,
            "brightness": self.brightness,
            "busy": self.busy,
            "busy_params": dict(self.busy_params),
            "active_mode": self.active_mode,
            "mode_params": dict(self.mode_params),
            "cycle_index": self.cycle_index,
            "static_pixels": self.static_pixels,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModeState:
        return cls(
            power=bool(data.get("power", True)),
            brightness=float(data.get("brightness", 0.65)),
            busy=bool(data.get("busy", False)),
            busy_params=dict(
                data.get("busy_params") or {"color": "#FF1800", "speed": 0.35, "min_level": 0.08}
            ),
            active_mode=str(data.get("active_mode", "rainbow")),
            mode_params=dict(
                data.get("mode_params") or {"speed": 0.05, "scale": 1.0, "axis": "u"}
            ),
            cycle_index=int(data.get("cycle_index", 0)),
            static_pixels=data.get("static_pixels"),
        )
