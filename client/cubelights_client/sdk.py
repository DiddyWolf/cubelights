"""High-level Python SDK for controlling Cubelights from Windows 11."""

from __future__ import annotations

import time
from typing import Any, Iterable

from cubelights_shared.patterns import KeyframePattern, RGB, parse_color
from cubelights_shared.protocol import FRAME_FLAG_NONE, FRAME_FLAG_PERSIST_STATIC, FramePacket
from cubelights_shared.segments import SegmentSpec

from .config import ClientConfig
from .transport import TransportBackend, connect_best_transport


class FrameStreamer:
    """Context manager for paced high-FPS binary LED frame streaming."""

    def __init__(self, client: CubeLightsClient, fps: float = 45.0, brightness: float = 1.0) -> None:
        self.client = client
        self.fps = max(1.0, float(fps))
        self.brightness = max(0.0, min(1.0, float(brightness)))
        self._period = 1.0 / self.fps
        self._next_tick = 0.0

    def __enter__(self) -> FrameStreamer:
        self._next_tick = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def push(self, colors: list[RGB], brightness: float | None = None) -> None:
        """Send a frame and sleep as needed to maintain target FPS."""
        bri = self.brightness if brightness is None else max(0.0, min(1.0, float(brightness)))
        self.client.send_frame(colors, brightness=bri)
        self._next_tick += self._period
        sleep_s = self._next_tick - time.monotonic()
        if sleep_s > 0:
            time.sleep(sleep_s)
        else:
            self._next_tick = time.monotonic()


class CubeLightsClient:
    """Ergonomic client for controlling the Cubelights Raspberry Pi daemon."""

    def __init__(self, transport: TransportBackend, auto_sync_time: bool = True) -> None:
        self.transport = transport
        if auto_sync_time:
            try:
                self.sync_time()
            except Exception:
                pass

    @classmethod
    def connect(
        cls,
        host: str | None = None,
        tcp_port: int | None = None,
        udp_port: int | None = None,
        bt_mac: str | None = None,
        rfcomm_channel: int | None = None,
        serial_port: str | None = None,
        prefer_rfcomm: bool = False,
        auto_sync_time: bool = True,
    ) -> CubeLightsClient:
        cfg = ClientConfig.load()
        tx = connect_best_transport(
            pan_ip=host or cfg.pan_ip,
            tcp_port=tcp_port or cfg.tcp_port,
            udp_port=udp_port if udp_port is not None else cfg.udp_port,
            bt_mac=bt_mac if bt_mac is not None else cfg.bt_mac,
            rfcomm_channel=rfcomm_channel or cfg.rfcomm_channel,
            serial_port=serial_port if serial_port is not None else cfg.serial_port,
            prefer_rfcomm=prefer_rfcomm,
        )
        return cls(tx, auto_sync_time=auto_sync_time)

    def __enter__(self) -> CubeLightsClient:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        self.transport.close()

    @property
    def transport_name(self) -> str:
        return self.transport.name

    def status(self) -> dict[str, Any]:
        return self.transport.send_command("status")

    def ping(self) -> dict[str, Any]:
        return self.transport.send_command("ping")

    def sync_time(self) -> dict[str, Any]:
        return self.transport.send_command("sync_time", epoch_ms=int(time.time() * 1000))

    def set_power(self, on: bool) -> dict[str, Any]:
        return self.transport.send_command("set_power", on=bool(on))

    def set_brightness(self, level: float) -> dict[str, Any]:
        return self.transport.send_command("set_brightness", level=max(0.0, min(1.0, float(level))))

    def set_busy(self, enabled: bool, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"enabled": bool(enabled)}
        if params is not None:
            payload["params"] = params
        return self.transport.send_command("set_busy", **payload)

    def set_mode(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        return self.transport.send_command(
            "set_mode",
            name=name,
            params=params or {},
            persist=persist,
        )

    def set_leds(
        self,
        updates: list[dict[str, Any]] | None = None,
        colors: Iterable[Any] | None = None,
        clear_rest: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"clear_rest": clear_rest}
        if updates is not None:
            payload["updates"] = updates
        if colors is not None:
            payload["colors"] = [list(parse_color(c)) for c in colors]
        return self.transport.send_command("set_leds", **payload)

    def send_frame(
        self,
        colors: list[RGB],
        brightness: float = 1.0,
        persist_static: bool = False,
    ) -> None:
        bri_byte = max(0, min(255, int(round(max(0.0, min(1.0, brightness)) * 255))))
        flags = FRAME_FLAG_PERSIST_STATIC if persist_static else FRAME_FLAG_NONE
        pkt = FramePacket.from_rgb_list(colors, brightness=bri_byte, flags=flags)
        self.transport.send_frame(pkt)

    def stream(self, fps: float = 45.0, brightness: float = 1.0) -> FrameStreamer:
        return FrameStreamer(self, fps=fps, brightness=brightness)

    def upload_pattern(self, pattern: KeyframePattern | dict[str, Any], activate: bool = True) -> dict[str, Any]:
        pat_dict = pattern.to_dict() if isinstance(pattern, KeyframePattern) else pattern
        return self.transport.send_command("upload_pattern", pattern=pat_dict, activate=activate)

    def list_patterns(self) -> list[dict[str, Any]]:
        res = self.transport.send_command("list_patterns")
        return list(res.get("patterns", []))

    def delete_pattern(self, name: str) -> dict[str, Any]:
        return self.transport.send_command("delete_pattern", name=name)

    def get_config(self) -> dict[str, Any]:
        res = self.transport.send_command("get_config")
        return dict(res.get("config", {}))

    def set_segment_map(self, segments: list[SegmentSpec | dict[str, Any]]) -> dict[str, Any]:
        serialized = [s.to_dict() if isinstance(s, SegmentSpec) else s for s in segments]
        return self.transport.send_command("set_segment_map", segments=serialized)

    def set_color_order(self, color_order: str) -> dict[str, Any]:
        return self.transport.send_command("set_color_order", color_order=color_order)

    def calibrate(self, duration_s: float = 8.0) -> dict[str, Any]:
        return self.transport.send_command("calibrate", duration_s=duration_s)

    def enter_pairing(self) -> dict[str, Any]:
        return self.transport.send_command("enter_pairing")
