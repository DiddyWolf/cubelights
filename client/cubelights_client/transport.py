"""Dual-transport connectivity (Bluetooth PAN TCP/UDP with automatic RFCOMM fallback)."""

from __future__ import annotations

import logging
import socket
import threading
import uuid
from typing import Any, Protocol

from cubelights_shared.protocol import (
    DEFAULT_PAN_IP,
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    FramePacket,
    RFCOMM_CONTROL_CHANNEL,
    StreamDecoder,
    encode_json_command,
)

logger = logging.getLogger(__name__)


class TransportError(RuntimeError):
    """Raised when no transport can connect or a command fails on the wire."""


class TransportBackend(Protocol):
    """Underlying connected transport interface."""

    @property
    def name(self) -> str: ...

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def send_command(self, cmd: str, **payload: Any) -> dict[str, Any]: ...

    def send_frame(self, pkt: FramePacket) -> None: ...


class _StreamTransportBase:
    """Shared request/response and frame-sending mechanics for stream sockets."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._decoder = StreamDecoder()
        self._connected = False

    def _raw_write(self, data: bytes) -> None:
        raise NotImplementedError

    def _raw_read(self, max_bytes: int = 4096) -> bytes:
        raise NotImplementedError

    def send_frame(self, pkt: FramePacket) -> None:
        with self._lock:
            if not self._connected:
                raise TransportError("Transport is not connected")
            try:
                self._raw_write(pkt.encode())
            except OSError as exc:
                self._connected = False
                raise TransportError(f"Frame write failed: {exc}") from exc

    def send_command(self, cmd: str, **payload: Any) -> dict[str, Any]:
        req_id = uuid.uuid4().hex[:8]
        wire = encode_json_command(cmd, req_id=req_id, **payload)
        with self._lock:
            if not self._connected:
                raise TransportError("Transport is not connected")
            try:
                self._raw_write(wire)
                while True:
                    chunk = self._raw_read(4096)
                    if not chunk:
                        raise TransportError("Connection closed by peer")
                    for item in self._decoder.feed(chunk):
                        if isinstance(item, dict):
                            if item.get("id") in (req_id, None):
                                if not item.get("ok", False):
                                    raise TransportError(str(item.get("error", "Command failed")))
                                return dict(item.get("data") or {})
            except OSError as exc:
                self._connected = False
                raise TransportError(f"Command {cmd!r} failed: {exc}") from exc


class TcpPanTransport(_StreamTransportBase):
    """IP transport over Bluetooth PAN (TCP for commands, optional UDP for frames)."""

    def __init__(
        self,
        host: str = DEFAULT_PAN_IP,
        tcp_port: int = DEFAULT_TCP_PORT,
        udp_port: int = DEFAULT_UDP_PORT,
        connect_timeout_s: float = 0.45,
        io_timeout_s: float = 3.0,
        use_udp_frames: bool = True,
    ) -> None:
        super().__init__()
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.connect_timeout_s = connect_timeout_s
        self.io_timeout_s = io_timeout_s
        self.use_udp_frames = use_udp_frames
        self._tcp: socket.socket | None = None
        self._udp: socket.socket | None = None

    @property
    def name(self) -> str:
        return f"pan-ip:{self.host}:{self.tcp_port}"

    def connect(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout_s)
        try:
            sock.connect((self.host, self.tcp_port))
            sock.settimeout(self.io_timeout_s)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            sock.close()
            raise TransportError(f"PAN TCP connect to {self.host}:{self.tcp_port} failed: {exc}") from exc

        self._tcp = sock
        if self.use_udp_frames and self.udp_port > 0:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._decoder.clear()
        self._connected = True

    def close(self) -> None:
        self._connected = False
        for sock in (self._tcp, self._udp):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._tcp = None
        self._udp = None

    def _raw_write(self, data: bytes) -> None:
        assert self._tcp is not None
        self._tcp.sendall(data)

    def _raw_read(self, max_bytes: int = 4096) -> bytes:
        assert self._tcp is not None
        return self._tcp.recv(max_bytes)

    def send_frame(self, pkt: FramePacket) -> None:
        if self.use_udp_frames and self._udp is not None and self._connected:
            try:
                self._udp.sendto(pkt.encode(), (self.host, self.udp_port))
                return
            except OSError:
                pass
        super().send_frame(pkt)


class RfcommBluetoothTransport(_StreamTransportBase):
    """Direct Classic Bluetooth RFCOMM transport via Python socket.AF_BLUETOOTH."""

    def __init__(
        self,
        bt_mac: str,
        channel: int = RFCOMM_CONTROL_CHANNEL,
        timeout_s: float = 5.0,
    ) -> None:
        super().__init__()
        self.bt_mac = bt_mac
        self.channel = channel
        self.timeout_s = timeout_s
        self._sock: socket.socket | None = None

    @property
    def name(self) -> str:
        return f"rfcomm:{self.bt_mac}/ch{self.channel}"

    def connect(self) -> None:
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise TransportError("Python socket.AF_BLUETOOTH is not supported on this platform")
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(self.timeout_s)
        try:
            sock.connect((self.bt_mac, self.channel))
        except OSError as exc:
            sock.close()
            raise TransportError(
                f"Bluetooth RFCOMM connect to {self.bt_mac} ch {self.channel} failed: {exc}"
            ) from exc
        self._sock = sock
        self._decoder.clear()
        self._connected = True

    def close(self) -> None:
        self._connected = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _raw_write(self, data: bytes) -> None:
        assert self._sock is not None
        self._sock.sendall(data)

    def _raw_read(self, max_bytes: int = 4096) -> bytes:
        assert self._sock is not None
        return self._sock.recv(max_bytes)


class SerialComTransport(_StreamTransportBase):
    """Optional Windows virtual Bluetooth COM port transport via pyserial."""

    def __init__(self, port: str, baudrate: int = 115200, timeout_s: float = 3.0) -> None:
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self._ser = None

    @property
    def name(self) -> str:
        return f"serial:{self.port}"

    def connect(self) -> None:
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TransportError("pyserial is required for COM port transport") from exc

        try:
            self._ser = serial.Serial(self.port, baudrate=self.baudrate, timeout=self.timeout_s)
        except Exception as exc:
            raise TransportError(f"Could not open serial port {self.port}: {exc}") from exc
        self._decoder.clear()
        self._connected = True

    def close(self) -> None:
        self._connected = False
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def _raw_write(self, data: bytes) -> None:
        assert self._ser is not None
        self._ser.write(data)
        self._ser.flush()

    def _raw_read(self, max_bytes: int = 4096) -> bytes:
        assert self._ser is not None
        chunk = self._ser.read(1)
        if not chunk:
            return b""
        waiting = getattr(self._ser, "in_waiting", 0)
        if waiting:
            chunk += self._ser.read(min(max_bytes - 1, waiting))
        return chunk


def connect_best_transport(
    pan_ip: str = DEFAULT_PAN_IP,
    tcp_port: int = DEFAULT_TCP_PORT,
    udp_port: int = DEFAULT_UDP_PORT,
    bt_mac: str | None = None,
    rfcomm_channel: int = RFCOMM_CONTROL_CHANNEL,
    serial_port: str | None = None,
    prefer_rfcomm: bool = False,
) -> TransportBackend:
    """Attempt PAN IP first (unless prefer_rfcomm=True), then RFCOMM, then COM port."""
    candidates: list[TransportBackend] = []
    pan = TcpPanTransport(host=pan_ip, tcp_port=tcp_port, udp_port=udp_port)
    rfcomm = RfcommBluetoothTransport(bt_mac=bt_mac, channel=rfcomm_channel) if bt_mac else None
    serial_tx = SerialComTransport(port=serial_port) if serial_port else None

    if prefer_rfcomm:
        if rfcomm:
            candidates.append(rfcomm)
        candidates.append(pan)
    else:
        candidates.append(pan)
        if rfcomm:
            candidates.append(rfcomm)
    if serial_tx:
        candidates.append(serial_tx)

    errors: list[str] = []
    for tx in candidates:
        try:
            tx.connect()
            logger.debug("Connected via %s", tx.name)
            return tx
        except TransportError as exc:
            errors.append(f"{tx.name}: {exc}")
            tx.close()

    raise TransportError("Could not connect to Cubelights Pi (" + "; ".join(errors) + ")")
