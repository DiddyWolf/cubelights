"""Lightweight local HTTP server and JSON API for the Cubelights Web UI."""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import ClientConfig
from .sdk import CubeLightsClient

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent / "web"


class ClientBridge:
    """Thread-safe lazy/reconnecting wrapper around CubeLightsClient."""

    def __init__(self, config: ClientConfig | None = None) -> None:
        self.config = config or ClientConfig.load()
        self._lock = threading.RLock()
        self._client: CubeLightsClient | None = None

    def _ensure_client(self) -> CubeLightsClient:
        if self._client is None:
            self._client = CubeLightsClient.connect(
                host=self.config.pan_ip,
                tcp_port=self.config.tcp_port,
                udp_port=self.config.udp_port,
                bt_mac=self.config.bt_mac,
                rfcomm_channel=self.config.rfcomm_channel,
                serial_port=self.config.serial_port,
            )
        return self._client

    def call(self, fn):
        with self._lock:
            last_exc: Exception | None = None
            for attempt in range(2):
                try:
                    client = self._ensure_client()
                    return fn(client), client.transport_name
                except Exception as exc:
                    last_exc = exc
                    if self._client is not None:
                        self._client.close()
                        self._client = None
                    if attempt == 1:
                        break
            assert last_exc is not None
            raise last_exc

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None


def create_web_server(
    bridge: ClientBridge,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create the localhost Web UI HTTP server."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("WebUI: " + format, *args)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                html_path = WEB_DIR / "index.html"
                data = html_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            if self.path == "/api/status":
                try:
                    (status_data, cfg_data, patterns), tx_name = bridge.call(
                        lambda cl: (cl.status(), cl.get_config(), cl.list_patterns())
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "transport": tx_name,
                            "status": status_data,
                            "config": cfg_data,
                            "patterns": patterns,
                        },
                    )
                except Exception as exc:
                    self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:
            if self.path != "/api/command":
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
                cmd = str(body.pop("cmd"))
                res, tx_name = bridge.call(lambda cl: cl.transport.send_command(cmd, **body))
                self._send_json(HTTPStatus.OK, {"ok": True, "transport": tx_name, "data": res})
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)
