"""Command-line interface (`cubelights`) for Windows 11 laptop control."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cubelights_shared.patterns import parse_color

from .config import ClientConfig, discover_paired_pis, format_bt_mac
from .sdk import CubeLightsClient
from .tray import main as tray_main


def _connect_from_args(args: argparse.Namespace) -> CubeLightsClient:
    return CubeLightsClient.connect(
        host=args.host,
        bt_mac=args.mac,
        serial_port=args.serial,
        prefer_rfcomm=args.rfcomm,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cubelights", description="Control your Cubelights Raspberry Pi over Bluetooth")
    p.add_argument("--host", default=None, help="Override PAN IP (default 10.55.0.1)")
    p.add_argument("--mac", default=None, help="Override Bluetooth MAC for RFCOMM")
    p.add_argument("--serial", default=None, help="Override virtual COM port (e.g. COM5)")
    p.add_argument("--rfcomm", action="store_true", help="Prefer RFCOMM Bluetooth over PAN IP")

    sub = p.add_subparsers(dest="command", required=True)

    disc = sub.add_parser("discover", help="Find paired Bluetooth Cubelights devices on Windows and optionally save MAC")
    disc.add_argument("--save-mac", default=None, help="Save Bluetooth MAC to client config")

    sub.add_parser("status", help="Show Pi LED engine status")

    cal = sub.add_parser("calibrate", help="Run color-order & segment-direction calibration sequence")
    cal.add_argument("--duration", type=float, default=8.0)

    mode = sub.add_parser("mode", help="Set active LED mode")
    mode.add_argument("name", help="Shader name (rainbow, wave, seasonal, pulse, warm_desk, sparkle, chase, solid, pattern:<name>)")
    mode.add_argument("--params", default="{}", help="JSON dict of shader parameters")

    busy = sub.add_parser("busy", help="Toggle Busy / On-Call pulsing red override")
    busy.add_argument("state", choices=["on", "off"])

    pwr = sub.add_parser("power", help="Turn lights on or off")
    pwr.add_argument("state", choices=["on", "off"])

    bri = sub.add_parser("brightness", help="Set brightness (0.0 to 1.0 or 0..100%%)")
    bri.add_argument("level", type=float)

    sled = sub.add_parser("set-led", help="Set a single LED index (0..28) to an RGB color")
    sled.add_argument("index", type=int)
    sled.add_argument("color", help="Hex '#RRGGBB' or comma 'R,G,B'")
    sled.add_argument("--clear-rest", action="store_true")

    szone = sub.add_parser("set-zone", help="Set a named segment/zone to an RGB color")
    szone.add_argument("zone", help="Zone name (e.g. strip_1, strip_2, strip_3)")
    szone.add_argument("color", help="Hex '#RRGGBB' or comma 'R,G,B'")
    szone.add_argument("--clear-rest", action="store_true")

    up = sub.add_parser("upload", help="Upload a declarative JSON keyframe pattern file")
    up.add_argument("file", type=Path)
    up.add_argument("--no-activate", action="store_true")

    sub.add_parser("patterns", help="List uploaded patterns on /data")

    dp = sub.add_parser("delete-pattern", help="Delete an uploaded pattern")
    dp.add_argument("name")

    co = sub.add_parser("color-order", help="Set APA102/SK9822 byte order (e.g. BGR, RGB)")
    co.add_argument("order")

    tray = sub.add_parser("tray", help="Start Windows system tray + local Web UI")
    tray.add_argument("--no-browser", action="store_true")
    tray.add_argument("--port", type=int, default=None)

    return p


def _parse_cli_color(raw: str) -> list[int]:
    if "," in raw:
        parts = [int(x.strip()) for x in raw.split(",")]
        return list(parse_color(parts))
    return list(parse_color(raw))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "discover":
        if args.save_mac:
            cfg = ClientConfig.load()
            cfg.bt_mac = format_bt_mac(args.save_mac)
            saved = cfg.save()
            print(f"Saved bt_mac={cfg.bt_mac} to {saved}")
            return 0
        devices = discover_paired_pis()
        if not devices:
            print("No paired Bluetooth devices found via Windows PnP.")
        else:
            for dev in devices:
                print(f"{dev['name']}: {dev['mac']}")
        return 0

    if args.command == "tray":
        tray_args: list[str] = []
        if args.no_browser:
            tray_args.append("--no-browser")
        if args.port is not None:
            tray_args.extend(["--port", str(args.port)])
        return tray_main(tray_args)

    with _connect_from_args(args) as cl:
        print(f"[via {cl.transport_name}]", file=sys.stderr)
        if args.command == "status":
            print(json.dumps(cl.status(), indent=2))
        elif args.command == "calibrate":
            print(json.dumps(cl.calibrate(duration_s=args.duration), indent=2))
        elif args.command == "mode":
            params: dict[str, Any] = json.loads(args.params)
            print(json.dumps(cl.set_mode(args.name, params=params), indent=2))
        elif args.command == "busy":
            print(json.dumps(cl.set_busy(args.state == "on"), indent=2))
        elif args.command == "power":
            print(json.dumps(cl.set_power(args.state == "on"), indent=2))
        elif args.command == "brightness":
            lvl = args.level / 100.0 if args.level > 1.0 else args.level
            print(json.dumps(cl.set_brightness(lvl), indent=2))
        elif args.command == "set-led":
            rgb = _parse_cli_color(args.color)
            print(json.dumps(cl.set_leds(updates=[{"index": args.index, "color": rgb}], clear_rest=args.clear_rest), indent=2))
        elif args.command == "set-zone":
            rgb = _parse_cli_color(args.color)
            print(json.dumps(cl.set_leds(updates=[{"zone": args.zone, "color": rgb}], clear_rest=args.clear_rest), indent=2))
        elif args.command == "upload":
            pat = json.loads(args.file.read_text(encoding="utf-8"))
            print(json.dumps(cl.upload_pattern(pat, activate=not args.no_activate), indent=2))
        elif args.command == "patterns":
            print(json.dumps(cl.list_patterns(), indent=2))
        elif args.command == "delete-pattern":
            print(json.dumps(cl.delete_pattern(args.name), indent=2))
        elif args.command == "color-order":
            print(json.dumps(cl.set_color_order(args.order), indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
