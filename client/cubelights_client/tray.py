"""Windows 11 System Tray app hosting the Web UI, Busy hotkey, and Lock-screen watcher."""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

from .config import ClientConfig
from .web_ui import ClientBridge, create_web_server
from .windows_hooks import WindowsHookRunner

logger = logging.getLogger("cubelights.tray")


def _make_icon_image(color: tuple[int, int, int] = (56, 189, 248)):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (15, 18, 24, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((6, 6, 58, 58), radius=12, fill=(24, 30, 42, 255), outline=color, width=5)
    draw.ellipse((20, 20, 44, 44), fill=color)
    return img


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cubelights System Tray & Local Web UI")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    parser.add_argument("--port", type=int, default=None, help="Local Web UI port (default 8765)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg = ClientConfig.load()
    port = args.port or cfg.web_port
    bridge = ClientBridge(cfg)
    httpd = create_web_server(bridge, host="127.0.0.1", port=port)

    web_thread = threading.Thread(target=httpd.serve_forever, name="cubelights-webui", daemon=True)
    web_thread.start()
    url = f"http://127.0.0.1:{port}/"
    logger.info("Local Web UI running at %s", url)

    state_lock = threading.Lock()
    was_on_before_lock = {"value": True}

    def run_cmd(fn) -> None:
        try:
            bridge.call(fn)
        except Exception as exc:
            logger.warning("Tray command failed: %s", exc)

    def on_toggle_busy() -> None:
        def _toggle(cl):
            st = cl.status()
            return cl.set_busy(not bool(st.get("busy", False)))

        run_cmd(_toggle)

    def on_lock() -> None:
        if not cfg.auto_off_on_lock:
            return
        with state_lock:
            try:
                (st, _tx) = bridge.call(lambda cl: cl.status())
                was_on_before_lock["value"] = bool(st.get("power", True))
                if was_on_before_lock["value"]:
                    bridge.call(lambda cl: cl.set_power(False))
            except Exception as exc:
                logger.debug("Auto-off on lock skipped: %s", exc)

    def on_unlock() -> None:
        if not cfg.auto_off_on_lock:
            return
        with state_lock:
            if was_on_before_lock["value"]:
                run_cmd(lambda cl: cl.set_power(True))

    hooks = WindowsHookRunner(
        on_lock=on_lock,
        on_unlock=on_unlock,
        hotkey=cfg.busy_hotkey,
        on_hotkey=on_toggle_busy,
    )
    hooks.start()

    try:
        import pystray
    except ImportError:
        logger.warning("pystray not installed; running Web UI headless without tray icon.")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            web_thread.join()
        except KeyboardInterrupt:
            pass
        finally:
            hooks.stop()
            httpd.shutdown()
            bridge.close()
        return 0

    menu = pystray.Menu(
        pystray.MenuItem("Open Control Panel", lambda _icon, _item: webbrowser.open(url), default=True),
        pystray.MenuItem("Toggle Busy / On-Call", lambda _icon, _item: on_toggle_busy()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Modes",
            pystray.Menu(
                pystray.MenuItem("Rainbow", lambda _i, _m: run_cmd(lambda cl: cl.set_mode("rainbow", {"speed": 0.05}))),
                pystray.MenuItem("Blue Waves", lambda _i, _m: run_cmd(lambda cl: cl.set_mode("wave"))),
                pystray.MenuItem("Seasonal (Auto)", lambda _i, _m: run_cmd(lambda cl: cl.set_mode("seasonal", {"theme": "auto"}))),
                pystray.MenuItem("Warm Desk", lambda _i, _m: run_cmd(lambda cl: cl.set_mode("warm_desk"))),
                pystray.MenuItem("Sparkle", lambda _i, _m: run_cmd(lambda cl: cl.set_mode("sparkle"))),
            ),
        ),
        pystray.MenuItem("Lights On", lambda _i, _m: run_cmd(lambda cl: cl.set_power(True))),
        pystray.MenuItem("Lights Off", lambda _i, _m: run_cmd(lambda cl: cl.set_power(False))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, _item: icon.stop()),
    )

    icon = pystray.Icon("cubelights", _make_icon_image(), "Cubelights", menu)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        icon.run()
    finally:
        hooks.stop()
        httpd.shutdown()
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
