# Cubelights

Bluetooth-controlled cubicle RGB LED lighting system built for a **Raspberry Pi Zero W v1.1**, **Maniacal Labs PiPixel HAT**, **Pimoroni Button SHIM**, and **3 series-wired APA102/SK9822 LED strips (6 + 13 + 10 = 29 LEDs)**, paired with a **Windows 11** client suite.

## Features
- **Physical Button SHIM Control** (`I2C 0x3F`):
  - `A`: Next mode (`rainbow`, `wave`, `seasonal`, `warm_desk`, `sparkle`, `chase`, etc.)
  - `B`: Toggle **Busy / On-Call** override (slow pulsing red)
  - `C`: Cycle brightness (`20% → 40% → 60% → 80% → 100%` of safety cap)
  - `D`: Previous mode
  - `E` (short press): Toggle lights On/Off
  - `E` (hold 2s): Open 120s Bluetooth pairing window (SHIM LED pulses blue)
  - `A + E` (hold 3s): Safe shutdown (`systemctl poweroff`)
- **Hybrid Bluetooth Connectivity (No Office Wi-Fi Required)**:
  - **Bluetooth PAN (NAP)** (`10.55.0.1/24`) for native `ssh`, `scp`, and low-latency TCP/UDP LED control.
  - **RFCOMM Channel 1 (Serial Console)** for VPN/policy-proof maintenance login over a Windows virtual COM port.
  - **RFCOMM Channel 2 (LED Control)** speaking the exact same dual-framing protocol as IP, with automatic client fallback if corporate VPN blocks PAN.
- **Corruption-Resilient Filesystem**:
  - Read-only root filesystem (`OverlayFS`) + dedicated `/data` partition mounted `ro` by default and transiently remounted `rw` only during debounced atomic saves.
- **Layered Extensible LED Engine**:
  - Built-in parameterized shaders (`rainbow`, `pulse`, `wave`, `seasonal`, `warm_desk`, `sparkle`, `chase`, `gradient`, `solid`) mapped over configurable `6 + 13 + 10` segment coordinates.
  - Uploadable declarative JSON keyframe animations stored on `/data/patterns/`.
  - High-speed compact binary frame streaming (`0x01`, 92 bytes/frame) for direct static or 45–60 FPS animated control from Python scripts.
  - Optional custom Python shaders in `/data/shaders/`.
- **Windows 11 Laptop Client**:
  - `cubelights` Python SDK & CLI, system tray app (`cubelights-tray`), local Web UI (`http://127.0.0.1:8765`), global `Ctrl+Alt+B` Busy hotkey, Windows lock-screen auto-off, and automatic offline clock sync (`sync_time`).

## Repository Structure

```text
cubelights/
├── SPEC.md                      # Technical specification & architecture
├── README.md                    # Project overview
├── docs/
│   ├── 01-hardware-assembly.md  # Pi Zero W, PiPixel SPI_DAT, Button SHIM, 6+13+10 wiring
│   ├── 02-pi-provisioning.md    # 32-bit Trixie/Bookworm, prepare_bootfs.ps1, setup_pi.sh, OverlayFS
│   ├── 03-bluetooth-and-windows.md # Pairing, PAN SSH, RFCOMM console, Windows CLI/Tray/Web UI
│   ├── 04-maintenance-and-backups.md # OverlayFS updates, /data edits, Golden SD backups, PiUART recovery
│   ├── 05-patterns-and-protocol.md   # Wire protocol, JSON keyframes, custom shaders & SDK streaming
│   └── 06-testing-and-troubleshooting.md # Boot/PiUART/HAT triage, BT E2E, OverlayFS power-cut tests
├── shared/                      # cubelights-shared (protocol, segments, patterns)
├── pi/                          # cubelights-pi (daemon, APA102 SPI, Button SHIM I2C, BlueZ, system/)
├── client/                      # cubelights-client (Windows 11 SDK, CLI, Tray, Web UI)
├── patterns/                    # Curated example keyframe patterns
└── tests/                       # Automated unit & end-to-end tests
```

## Quick Start (Windows Dev / Local Simulation)

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -e .\shared -e .\pi -e .\client pytest
.\.venv\Scripts\pytest.exe -v .\tests
```

You can also run the Pi daemon locally in mock-hardware mode and test the Web UI right on Windows:

```powershell
# Terminal 1: mock Pi daemon on localhost
.\.venv\Scripts\cubelights-daemon.exe --mock-hw --no-bt --data-dir .\.cubelights-dev-data --host 127.0.0.1

# Terminal 2: CLI or Web UI pointed at 127.0.0.1
.\.venv\Scripts\cubelights.exe --host 127.0.0.1 status
.\.venv\Scripts\cubelights.exe --host 127.0.0.1 mode seasonal --params '{\"theme\":\"halloween\"}'
```

For full Pi hardware assembly, provisioning, Bluetooth setup, maintenance, and SD card backup instructions, follow the numbered guides in [docs/](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/01-hardware-assembly.md).
