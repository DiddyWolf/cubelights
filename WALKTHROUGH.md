# Cubelights Implementation Walkthrough

## Summary of Changes

Implemented the complete `cubelights` monorepo in `c:\Users\DiddyWolf\Projects\cubelights`:

### 1. Specification & Documentation
- [SPEC.md](file:///c:/Users/DiddyWolf/Projects/cubelights/SPEC.md) — Approved architecture & wire protocol specification.
- [README.md](file:///c:/Users/DiddyWolf/Projects/cubelights/README.md) — Project overview, features, and local simulation quick start.
- [docs/01-hardware-assembly.md](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/01-hardware-assembly.md) — Pinout (`SPI0`, `I2C 0x3F`, optional `PiUART`), PiPixel `SPI_DAT` jumper, power budget, and `6 + 13 + 10` segment mapping.
- [docs/02-pi-provisioning.md](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/02-pi-provisioning.md) — Flashing 32-bit Bookworm Lite, `/data` partition setup, `setup_pi.sh`, strip calibration, and OverlayFS activation.
- [docs/03-bluetooth-and-windows.md](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/03-bluetooth-and-windows.md) — Timed Button `E` pairing, PAN SSH/TCP/UDP, RFCOMM serial console & LED control fallback, CLI, tray, and Web UI.
- [docs/04-maintenance-and-backups.md](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/04-maintenance-and-backups.md) — OverlayFS maintenance (`cubelights-rw` / `cubelights-ro`), `/data` edits, golden SD card image creation/restoration, and PiUART recovery.
- [docs/05-patterns-and-protocol.md](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/05-patterns-and-protocol.md) — JSON + binary frame protocol, shader parameters, declarative keyframe schemas, custom `/data/shaders/*.py`, and Python SDK streaming.

### 2. Shared Package (`shared/cubelights_shared/`)
- [protocol.py](file:///c:/Users/DiddyWolf/Projects/cubelights/shared/cubelights_shared/protocol.py): Incremental `StreamDecoder` multiplexing `\n`-delimited JSON commands and `0x01` binary `FramePacket`s.
- [segments.py](file:///c:/Users/DiddyWolf/Projects/cubelights/shared/cubelights_shared/segments.py): `SegmentMap` and `default_segment_map()` modeling the `6 + 13 + 10` (29 LED) series chain with 2D coordinates, `u` path parameter, and per-segment reversal.
- [patterns.py](file:///c:/Users/DiddyWolf/Projects/cubelights/shared/cubelights_shared/patterns.py): `KeyframePattern` schema, target resolution (indices/ranges/zones), and easing functions.

### 3. Raspberry Pi Daemon & Provisioning (`pi/`)
- [apa102.py](file:///c:/Users/DiddyWolf/Projects/cubelights/pi/cubelights_pi/drivers/apa102.py): Pure-Python `spidev` APA102/SK9822 driver (5-bit global brightness + configurable byte order + safety cap) and `MockStripDriver`.
- [button_shim.py](file:///c:/Users/DiddyWolf/Projects/cubelights/pi/cubelights_pi/drivers/button_shim.py): `smbus2`/`smbus` TCA9554A driver for Pimoroni Button SHIM + bit-banged APA102 status LED, supporting short press, 1.8s long press (`E` BT pairing), and 2.8s `A+E` chord (safe shutdown).
- [shaders.py](file:///c:/Users/DiddyWolf/Projects/cubelights/pi/cubelights_pi/shaders.py) & [engine.py](file:///c:/Users/DiddyWolf/Projects/cubelights/pi/cubelights_pi/engine.py): Layered 45 FPS `LedEngine` (`stream` with timeout fallback > `busy` > `static` > `keyframe` pattern > built-in/custom shaders), offline `sync_time` offset, calibration mode, and SHIM status LED feedback.
- [storage.py](file:///c:/Users/DiddyWolf/Projects/cubelights/pi/cubelights_pi/storage.py): Debounced atomic `/data` persistence with `ro`/`rw` remount management when `/data` is a separate mountpoint.
- [server.py](file:///c:/Users/DiddyWolf/Projects/cubelights/pi/cubelights_pi/server.py), [bt_manager.py](file:///c:/Users/DiddyWolf/Projects/cubelights/pi/cubelights_pi/bt_manager.py), [daemon.py](file:///c:/Users/DiddyWolf/Projects/cubelights/pi/cubelights_pi/daemon.py): TCP/UDP server + BlueZ D-Bus PAN NAP (`pan0`), RFCOMM Ch 1 `agetty` PTY shell, RFCOMM Ch 2 LED control, and timed pairing agent.
- `pi/system/`: [setup_pi.sh](file:///c:/Users/DiddyWolf/Projects/cubelights/pi/system/setup_pi.sh), `systemd-networkd` & `dnsmasq` configs, [cubelights.service](file:///c:/Users/DiddyWolf/Projects/cubelights/pi/system/cubelights.service), and OverlayFS/backup helpers (`cubelights-enable-ro.sh`, `cubelights-rw`, `cubelights-ro`, `cubelights-prepare-image.sh`).

### 4. Windows 11 Client & Example Patterns (`client/`, `patterns/`)
- [transport.py](file:///c:/Users/DiddyWolf/Projects/cubelights/client/cubelights_client/transport.py) & [sdk.py](file:///c:/Users/DiddyWolf/Projects/cubelights/client/cubelights_client/sdk.py): Auto-transport (`TcpPanTransport` -> `RfcommBluetoothTransport` / `SerialComTransport`), `sync_time` on connect, `CubeLightsClient`, and `FrameStreamer`.
- [cli.py](file:///c:/Users/DiddyWolf/Projects/cubelights/client/cubelights_client/cli.py), [tray.py](file:///c:/Users/DiddyWolf/Projects/cubelights/client/cubelights_client/tray.py), [windows_hooks.py](file:///c:/Users/DiddyWolf/Projects/cubelights/client/cubelights_client/windows_hooks.py), [web_ui.py](file:///c:/Users/DiddyWolf/Projects/cubelights/client/cubelights_client/web_ui.py), & [index.html](file:///c:/Users/DiddyWolf/Projects/cubelights/client/cubelights_client/web/index.html): CLI, system tray, `Ctrl+Alt+B` Busy hotkey, lock-screen auto-off, and localhost Web UI.
- Curated patterns in `patterns/`: `aurora_cascade.json`, `busy_beacon.json`, `holiday_twinkle.json`, `halloween_flicker.json`.

---

## Verification Results

Ran `c:\Users\DiddyWolf\Projects\cubelights\.venv\Scripts\pytest.exe -v c:\Users\DiddyWolf\Projects\cubelights\tests`:
- **13 passed in 0.84s** (all mocked hardware + protocol + storage + end-to-end TCP/UDP & Web UI HTTP API tests).
- Verified `cubelights --help` and `cubelights-daemon --help` run cleanly on Windows.
- Verified all files in `pi/system/` use Unix LF (`\n`) line endings.

*Note*: Physical SPI/I2C hardware (`spidev`, `smbus`, Button SHIM), BlueZ D-Bus RFCOMM/PAN pairing, and Raspberry Pi OS OverlayFS have not yet been executed on the Raspberry Pi Zero W hardware—follow [docs/02-pi-provisioning.md](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/02-pi-provisioning.md) and [docs/03-bluetooth-and-windows.md](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/03-bluetooth-and-windows.md) to bring up and verify on hardware.
