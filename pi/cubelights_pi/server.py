"""Transport-agnostic command/frame server for TCP, UDP, and Bluetooth RFCOMM streams."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Any, Callable

from cubelights_shared.patterns import KeyframePattern
from cubelights_shared.protocol import (
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    FRAME_FLAG_PERSIST_STATIC,
    FramePacket,
    ProtocolError,
    StreamDecoder,
    encode_json_response,
)
from cubelights_shared.segments import SegmentSpec

from .engine import LedEngine
from .storage import StorageManager

logger = logging.getLogger(__name__)


class CommandDispatcher:
    """Dispatches decoded JSON commands and binary FramePackets to LedEngine."""

    def __init__(
        self,
        engine: LedEngine,
        storage: StorageManager | None = None,
        on_enter_pairing: Callable[[], None] | None = None,
    ) -> None:
        self.engine = engine
        self.storage = storage
        self.on_enter_pairing = on_enter_pairing

    def handle_frame(self, pkt: FramePacket) -> None:
        colors = pkt.to_rgb_list()
        persist = bool(pkt.flags & FRAME_FLAG_PERSIST_STATIC)
        self.engine.push_stream_frame(
            colors=colors,
            brightness=pkt.brightness / 255.0,
            persist_static=persist,
        )

    def handle_command(self, msg: dict[str, Any]) -> bytes:
        req_id = msg.get("id")
        cmd = str(msg.get("cmd", "")).strip()
        try:
            data = self._dispatch(cmd, msg)
            return encode_json_response(True, req_id=req_id, data=data)
        except Exception as exc:
            logger.debug("Command %r failed: %s", cmd, exc)
            return encode_json_response(False, req_id=req_id, error=str(exc))

    def _dispatch(self, cmd: str, msg: dict[str, Any]) -> dict[str, Any]:
        if cmd in ("hello", "status", "get_status"):
            return self.engine.get_status()
        if cmd == "ping":
            return {"pong": True, "ts": time.time()}
        if cmd == "sync_time":
            epoch_ms = int(msg["epoch_ms"])
            self.engine.sync_time(epoch_ms)
            return {"synced": True, "wall_epoch": self.engine.wall_epoch()}
        if cmd == "set_power":
            self.engine.set_power(bool(msg["on"]))
            return {"power": self.engine.state.power}
        if cmd == "set_brightness":
            self.engine.set_brightness(float(msg["level"]))
            return {"brightness": self.engine.state.brightness}
        if cmd == "set_busy":
            params = msg.get("params")
            self.engine.set_busy(bool(msg["enabled"]), params=params if isinstance(params, dict) else None)
            return {"busy": self.engine.state.busy}
        if cmd == "set_mode":
            self.engine.set_mode(
                name=str(msg["name"]),
                params=msg.get("params") if isinstance(msg.get("params"), dict) else None,
                persist=bool(msg.get("persist", True)),
            )
            return {"active_mode": self.engine.state.active_mode}
        if cmd == "set_leds":
            self.engine.set_static_leds(
                updates=msg.get("updates"),
                all_colors=msg.get("colors"),
                clear_rest=bool(msg.get("clear_rest", False)),
            )
            return {"active_mode": "static"}
        if cmd == "upload_pattern":
            pattern = KeyframePattern.from_dict(msg["pattern"])
            activate = bool(msg.get("activate", True))
            self.engine.upload_pattern(pattern, activate=activate)
            return {"uploaded": pattern.name, "activated": activate}
        if cmd == "list_patterns":
            patterns = self.storage.list_patterns() if self.storage is not None else []
            return {"patterns": patterns}
        if cmd == "delete_pattern":
            name = str(msg["name"])
            deleted = self.storage.delete_pattern(name) if self.storage is not None else False
            return {"deleted": deleted, "name": name}
        if cmd == "get_config":
            return {"config": self.engine.config.to_dict()}
        if cmd == "set_segment_map":
            raw_segs = msg["segments"]
            specs = [SegmentSpec.from_dict(s) for s in raw_segs]
            self.engine.update_segments(specs)
            return {"total_leds": self.engine.seg_map.total_leds, "zones": self.engine.seg_map.zone_names()}
        if cmd == "set_color_order":
            self.engine.update_color_order(str(msg["color_order"]))
            return {"color_order": self.engine.config.color_order}
        if cmd == "calibrate":
            duration_s = float(msg.get("duration_s", 8.0))
            self.engine.start_calibration(duration_s=duration_s)
            return {"calibrating": True, "duration_s": duration_s}
        if cmd == "enter_pairing":
            if self.on_enter_pairing is not None:
                self.on_enter_pairing()
            return {"pairing": True}
        raise ValueError(f"Unknown command: {cmd!r}")


class CubeLightsServer:
    """Serves the dual-framing protocol over TCP/UDP and external Bluetooth RFCOMM fds."""

    def __init__(
        self,
        dispatcher: CommandDispatcher,
        host: str = "0.0.0.0",
        tcp_port: int = DEFAULT_TCP_PORT,
        udp_port: int = DEFAULT_UDP_PORT,
    ) -> None:
        self.dispatcher = dispatcher
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port

        self._stop_evt = threading.Event()
        self._tcp_sock: socket.socket | None = None
        self._udp_sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._stop_evt.clear()

        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp.bind((self.host, self.tcp_port))
        tcp.listen(8)
        tcp.settimeout(0.5)
        self._tcp_sock = tcp
        self.tcp_port = tcp.getsockname()[1]

        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind((self.host, self.udp_port))
        udp.settimeout(0.5)
        self._udp_sock = udp
        self.udp_port = udp.getsockname()[1]

        t_tcp = threading.Thread(target=self._accept_tcp_loop, name="cubelights-tcp", daemon=True)
        t_udp = threading.Thread(target=self._udp_loop, name="cubelights-udp", daemon=True)
        t_tcp.start()
        t_udp.start()
        self._threads.extend([t_tcp, t_udp])
        logger.info("Listening on TCP %s:%d and UDP %s:%d", self.host, self.tcp_port, self.host, self.udp_port)

    def stop(self) -> None:
        self._stop_evt.set()
        for sock in (self._tcp_sock, self._udp_sock):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        for t in self._threads:
            t.join(timeout=1.5)
        self._threads.clear()

    def _accept_tcp_loop(self) -> None:
        assert self._tcp_sock is not None
        while not self._stop_evt.is_set():
            try:
                conn, addr = self._tcp_sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            t = threading.Thread(
                target=self.handle_stream_socket,
                args=(conn, f"tcp:{addr[0]}:{addr[1]}"),
                daemon=True,
            )
            t.start()

    def _udp_loop(self) -> None:
        assert self._udp_sock is not None
        while not self._stop_evt.is_set():
            try:
                data, _addr = self._udp_sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                pkt = FramePacket.decode_datagram(data)
                self.dispatcher.handle_frame(pkt)
            except ProtocolError as exc:
                logger.debug("Dropped malformed UDP datagram: %s", exc)

    def handle_stream_socket(self, conn: socket.socket, label: str = "stream") -> None:
        """Serve a connected TCP or RFCOMM stream socket until EOF."""
        logger.info("Client connected (%s)", label)
        conn.settimeout(1.0)
        decoder = StreamDecoder()
        try:
            while not self._stop_evt.is_set():
                try:
                    chunk = conn.recv(4096)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                try:
                    items = decoder.feed(chunk)
                except ProtocolError as exc:
                    err = encode_json_response(False, error=f"Protocol error: {exc}")
                    conn.sendall(err)
                    decoder.clear()
                    continue

                for item in items:
                    if isinstance(item, FramePacket):
                        self.dispatcher.handle_frame(item)
                    else:
                        resp = self.dispatcher.handle_command(item)
                        conn.sendall(resp)
        except OSError as exc:
            logger.debug("Stream closed (%s): %s", label, exc)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            logger.info("Client disconnected (%s)", label)

    def handle_raw_fd(self, fd: int, label: str = "rfcomm-control") -> None:
        """Serve a raw file descriptor (used by BlueZ D-Bus ProfileManager1 RFCOMM)."""
        logger.info("RFCOMM stream connected (%s, fd=%d)", label, fd)
        decoder = StreamDecoder()
        write_lock = threading.Lock()
        try:
            while not self._stop_evt.is_set():
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                try:
                    items = decoder.feed(chunk)
                except ProtocolError as exc:
                    err = encode_json_response(False, error=f"Protocol error: {exc}")
                    with write_lock:
                        os.write(fd, err)
                    decoder.clear()
                    continue

                for item in items:
                    if isinstance(item, FramePacket):
                        self.dispatcher.handle_frame(item)
                    else:
                        resp = self.dispatcher.handle_command(item)
                        with write_lock:
                            os.write(fd, resp)
        except OSError as exc:
            logger.debug("RFCOMM stream closed (%s): %s", label, exc)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            logger.info("RFCOMM stream disconnected (%s)", label)
