"""End-to-end integration tests: CubeLightsClient + Web UI HTTP API <-> CubeLightsServer + LedEngine."""

import json
import threading
import time
import urllib.request

from cubelights_shared.patterns import Keyframe, KeyframePattern, KeyframeTarget

from cubelights_pi.config import DeviceConfig, ModeState
from cubelights_pi.drivers.apa102 import MockStripDriver
from cubelights_pi.drivers.button_shim import MockButtonShimDriver
from cubelights_pi.engine import LedEngine
from cubelights_pi.server import CommandDispatcher, CubeLightsServer
from cubelights_pi.storage import StorageManager

from cubelights_client.config import ClientConfig
from cubelights_client.sdk import CubeLightsClient
from cubelights_client.transport import TcpPanTransport
from cubelights_client.web_ui import ClientBridge, create_web_server


def test_sdk_and_webui_end_to_end(tmp_path):
    storage = StorageManager(data_dir=tmp_path, remount_ro=False, debounce_s=0.0)
    storage.ensure_layout()
    cfg = DeviceConfig()
    state = ModeState()
    strip = MockStripDriver(num_leds=29, max_brightness_cap=cfg.max_brightness_cap)
    shim = MockButtonShimDriver()
    engine = LedEngine(config=cfg, state=state, strip=strip, shim=shim, storage=storage)
    dispatcher = CommandDispatcher(engine=engine, storage=storage)
    server = CubeLightsServer(dispatcher=dispatcher, host="127.0.0.1", tcp_port=0, udp_port=0)

    server.start()
    engine.start()
    try:
        tx = TcpPanTransport(host="127.0.0.1", tcp_port=server.tcp_port, udp_port=server.udp_port)
        tx.connect()
        with CubeLightsClient(tx) as cl:
            st = cl.status()
            assert st["total_leds"] == 29
            assert st["zones"] == ["strip_1", "strip_2", "strip_3"]

            # Direct static zone control
            cl.set_leds(updates=[{"zone": "strip_1", "color": "#FF0000"}], clear_rest=True)
            engine.render_tick()
            assert strip.last_frame[0] == (255, 0, 0)
            assert strip.last_frame[6] == (0, 0, 0)

            # High-FPS binary frame streaming over UDP/TCP
            stream_colors = [(0, 255, 128)] * 29
            with cl.stream(fps=60.0) as streamer:
                streamer.push(stream_colors)
            time.sleep(0.05)
            engine.render_tick()
            assert strip.last_frame == stream_colors

            # Upload declarative keyframe pattern
            pat = KeyframePattern(
                name="e2e_pat",
                keyframes=[
                    Keyframe(
                        duration_ms=1000,
                        fill=(0, 0, 0),
                        targets=[KeyframeTarget(zone="strip_2", color=(12, 34, 56))],
                    )
                ],
            )
            cl.upload_pattern(pat, activate=True)
            engine.render_tick()
            assert strip.last_frame[6] == (12, 34, 56)
            assert any(p["name"] == "e2e_pat" for p in cl.list_patterns())

        # Verify Web UI HTTP server against the live daemon
        ccfg = ClientConfig(pan_ip="127.0.0.1", tcp_port=server.tcp_port, udp_port=server.udp_port)
        bridge = ClientBridge(ccfg)
        httpd = create_web_server(bridge, host="127.0.0.1", port=0)
        web_port = httpd.server_address[1]
        t_web = threading.Thread(target=httpd.serve_forever, daemon=True)
        t_web.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{web_port}/api/status") as resp:
                data = json.loads(resp.read().decode("utf-8"))
            assert data["ok"] is True
            assert data["status"]["total_leds"] == 29

            req = urllib.request.Request(
                f"http://127.0.0.1:{web_port}/api/command",
                data=json.dumps({"cmd": "set_busy", "enabled": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                cmd_res = json.loads(resp.read().decode("utf-8"))
            assert cmd_res["ok"] is True
            assert cmd_res["data"]["busy"] is True
        finally:
            httpd.shutdown()
            httpd.server_close()
            bridge.close()
    finally:
        engine.stop()
        server.stop()


def test_windows_hook_runner_no_ctypes_overflow_on_win32():
    import sys

    if sys.platform != "win32":
        return

    from cubelights_client.windows_hooks import WindowsHookRunner

    unraisable: list[BaseException] = []
    prev_hook = sys.unraisablehook
    sys.unraisablehook = lambda args: unraisable.append(args.exc_value)
    runner = WindowsHookRunner(
        on_lock=lambda: None,
        on_unlock=lambda: None,
        hotkey="Ctrl+Alt+B",
        on_hotkey=lambda: None,
    )
    try:
        assert runner.start() is True
        time.sleep(0.25)
    finally:
        runner.stop()
        sys.unraisablehook = prev_hook

    assert unraisable == []
