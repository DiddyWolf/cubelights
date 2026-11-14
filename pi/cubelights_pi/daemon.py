"""Main daemon entrypoint for cubelights-pi."""

from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import threading
import time

from cubelights_shared.protocol import DEFAULT_TCP_PORT, DEFAULT_UDP_PORT

from .bt_manager import BluetoothManager
from .drivers.apa102 import APA102Driver, MockStripDriver
from .drivers.button_shim import ButtonEvent, HardwareButtonShimDriver, MockButtonShimDriver
from .engine import LedEngine
from .server import CommandDispatcher, CubeLightsServer
from .storage import StorageManager

logger = logging.getLogger("cubelights.daemon")


def _trigger_safe_shutdown(engine: LedEngine, storage: StorageManager) -> None:
    logger.warning("Safe shutdown triggered via Button SHIM A+E chord")
    engine.flash_shim((255, 80, 0), duration_s=2.0)
    storage.flush_pending_state()
    engine.stop()
    subprocess.run(["sudo", "-n", "/bin/systemctl", "poweroff"], check=False)


def _bind_button_actions(
    engine: LedEngine,
    storage: StorageManager,
    bt_mgr: BluetoothManager | None,
) -> callable:
    def handle_action(action: str) -> None:
        if action == "next_mode":
            engine.step_mode_cycle(+1)
        elif action == "prev_mode":
            engine.step_mode_cycle(-1)
        elif action == "toggle_busy":
            engine.toggle_busy()
        elif action == "cycle_brightness":
            engine.cycle_brightness()
        elif action == "toggle_power":
            engine.toggle_power()
        elif action == "bt_pairing":
            if bt_mgr is not None:
                bt_mgr.enter_pairing_mode(120.0)
            else:
                engine.set_pairing_indicator(True, 10.0)
        elif action == "safe_shutdown":
            _trigger_safe_shutdown(engine, storage)
        else:
            logger.warning("Unknown button action: %s", action)

    def on_button_event(evt: ButtonEvent) -> None:
        bindings = engine.config.button_bindings
        if evt.kind == "chord" and evt.buttons == frozenset({"A", "E"}):
            action = bindings.get("AE_long", "safe_shutdown")
            handle_action(action)
            return
        if len(evt.buttons) == 1:
            btn = next(iter(evt.buttons))
            key = f"{btn}_{evt.kind}"
            action = bindings.get(key)
            if action:
                handle_action(action)

    return on_button_event


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cubelights Raspberry Pi LED Daemon")
    parser.add_argument("--data-dir", default="/data", help="Persistent config/pattern directory")
    parser.add_argument("--remount-ro", action="store_true", help="Remount data-dir rw/ro around writes")
    parser.add_argument("--mock-hw", action="store_true", help="Use mock SPI/I2C drivers for dev testing")
    parser.add_argument("--no-bt", action="store_true", help="Disable BlueZ Bluetooth manager")
    parser.add_argument("--host", default="0.0.0.0", help="TCP/UDP bind address")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT)
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT)
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    storage = StorageManager(data_dir=args.data_dir, remount_ro=args.remount_ro)
    storage.ensure_layout()
    config = storage.load_config()
    state = storage.load_state()
    seg_map = config.segment_map()

    if args.mock_hw:
        strip = MockStripDriver(num_leds=seg_map.total_leds, max_brightness_cap=config.max_brightness_cap)
        shim = MockButtonShimDriver()
    else:
        strip = APA102Driver(
            num_leds=seg_map.total_leds,
            spi_bus=config.spi_bus,
            spi_device=config.spi_device,
            spi_speed_hz=config.spi_speed_hz,
            color_order=config.color_order,
            led_protocol=config.led_protocol,
            max_brightness_cap=config.max_brightness_cap,
        )
        shim = HardwareButtonShimDriver()

    engine = LedEngine(config=config, state=state, strip=strip, shim=shim, storage=storage)
    bt_mgr: BluetoothManager | None = None

    dispatcher = CommandDispatcher(
        engine=engine,
        storage=storage,
        on_enter_pairing=lambda: bt_mgr.enter_pairing_mode(120.0) if bt_mgr else engine.set_pairing_indicator(True, 10.0),
    )
    server = CubeLightsServer(
        dispatcher=dispatcher,
        host=args.host,
        tcp_port=args.tcp_port,
        udp_port=args.udp_port,
    )

    if not args.no_bt:
        bt_mgr = BluetoothManager(
            on_control_fd=server.handle_raw_fd,
            on_pairing_state=lambda active, dur: engine.set_pairing_indicator(active, dur),
            on_connection_state=engine.set_bluetooth_status,
        )

    shim.register_callback(_bind_button_actions(engine, storage, bt_mgr))

    stop_evt = threading.Event()

    def _on_signal(signum, _frame) -> None:
        logger.info("Received signal %d, shutting down", signum)
        stop_evt.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    server.start()
    shim.start()
    engine.start()
    if bt_mgr is not None:
        try:
            bt_mgr.start()
        except Exception:
            logger.exception("Bluetooth manager failed to start; continuing without Bluetooth.")

    try:
        while not stop_evt.is_set():
            time.sleep(0.5)
    finally:
        engine.stop()
        shim.stop()
        if bt_mgr is not None:
            bt_mgr.stop()
        server.stop()
        strip.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
