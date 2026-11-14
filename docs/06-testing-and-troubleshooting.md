# 6. Testing & Troubleshooting Guide

This guide covers hardware bring-up verification, serial/network diagnostics, Bluetooth PAN & RFCOMM testing on Windows 11, and OverlayFS resilience verification.

---

## 1. Boot, PiUART Serial Console & Wi-Fi Diagnostics

### Pi Zero W v1.1 Boot Timing
- On a single-core Pi Zero W v1.1 (512 MB RAM), **Raspberry Pi OS Trixie** first-boot `cloud-init` (SSH keys, user creation, NetworkManager setup) takes **2–4 minutes**. Irregular green ACT LED flashing indicates normal SD card boot activity.
- Wi-Fi on Pi Zero W v1.1 supports **2.4 GHz (802.11b/g/n) WPA2** only (no 5 GHz, no WPA3-only).

### Adafruit PiUART Console (`COMx` on Windows)
- **Settings:** `115200` baud, 8 data bits, 1 stop bit, No parity, Flow control **None**. Press `Enter` if the Pi already finished booting before the terminal opened.
- **Required `bootfs` settings** (automated by [prepare_bootfs.ps1](../pi/system/prepare_bootfs.ps1)):
  - `cmdline.txt` (one single line): contains `console=serial0,115200`
  - `config.txt`: `enable_uart=1` placed under a trailing `[all]` section (never under `[pi5]` / `[cm5]`)
- **GPIO check:** Confirm PiUART pins align squarely on physical pins 1–10 (GND, GPIO 14 TXD -> PiUART RX, GPIO 15 RXD -> PiUART TX). Avoid feeding 5V simultaneously from both PiUART and PiPixel if powered externally.

### Isolating PiPixel HAT Power/GPIO Issues vs OS Issues
If the Pi boots without the HAT but fails to boot or light up with the HAT installed:
1. Power off, remove the **PiPixel HAT**, and boot via the Pi Zero W micro-USB `PWR` port alone.
2. Verify Wi-Fi / PiUART works cleanly.
3. Re-seat the PiPixel HAT over the Button SHIM stack, confirming no shifted pin rows, the jumper is on **`SPI_DAT`**, and the 5V supply at the PiPixel barrel jack / screw terminal measures ~5.0V under load.

### NetworkManager & `pan0` Coexistence Check
On Raspberry Pi OS Trixie, `NetworkManager` must remain the sole manager for `wlan0` (`systemd-networkd` must stay disabled):
```bash
systemctl is-enabled systemd-networkd   # Expected: disabled (or masked)
systemctl status NetworkManager         # Expected: active (running)
systemctl status cubelights-pan0        # Expected: active (exited)
ip addr show pan0                       # Expected: 10.55.0.1/24 (or custom PAN_SUBNET_PREFIX)
sudo ss -lunp | grep dnsmasq            # Expected: DHCP on *:67 only (port=0 disables DNS :53)
```

---

## 2. Hardware Bring-Up Checklist (After `setup_pi.sh` + `sudo reboot`)

Run these checks on the Pi over SSH or PiUART:

1. **Storage partitions & `/data` mount:**
   ```bash
   lsblk                  # Expect mmcblk0p1 (512M), mmcblk0p2 (~8G /), mmcblk0p3 (512M /data)
   df -h / /data
   mount | grep /data     # Expect 'ro' mount option by default
   ```
2. **Kernel buses (SPI & I2C):**
   ```bash
   ls -l /dev/spidev0.*   # Expect /dev/spidev0.0
   sudo i2cdetect -y 1    # Expect 0x3f (Pimoroni Button SHIM TCA9554A)
   ```
3. **Daemon health:**
   ```bash
   sudo systemctl status cubelights --no-pager
   sudo journalctl -u cubelights -n 80 --no-pager
   ```
4. **Button SHIM physical test:**
   - `A` / `D`: Cycle modes (`rainbow`, `wave`, `seasonal`, `warm_desk`, `sparkle`, `chase`)
   - `B`: Toggle Busy / On-Call pulsing red (SHIM LED turns red)
   - `C`: Cycle brightness steps
   - `E` (short): Toggle LEDs Off/On
   - `E` (hold ~2s): SHIM LED pulses blue (120s Bluetooth pairing mode)
   - `A + E` (hold ~3s): Flashes amber/red and triggers safe `systemctl poweroff`
5. **LED calibration (color order & 6 / 13 / 10 direction):**
   ```bash
   /opt/cubelights/venv/bin/python -c "
   from cubelights_shared.protocol import encode_json_command
   import socket
   s = socket.create_connection(('127.0.0.1', 7777))
   s.sendall(encode_json_command('calibrate', duration_s=8.0))
   print(s.recv(1024).decode())
   "
   ```
   - Observe **Red** (0–2s), **Green** (2–4s), **Blue** (4–6s). If Red/Blue are swapped, update `"color_order": "RGB"` via `cubelights color-order RGB` or `/data/config.json`.
   - Observe **6–8s**: `strip_1` (6 LEDs Red), `strip_2` (13 LEDs Green), `strip_3` (10 LEDs Blue), each ramping dim -> bright. If any strip ramps backwards relative to your cubicle mounting, set `"reversed": true` for that segment in `/data/config.json`.

---

## 3. Windows 11 Bluetooth End-to-End Verification (Wi-Fi Off Simulation)

Simulate your work cubicle by disabling home Wi-Fi temporarily on the Pi (`sudo nmcli radio wifi off`) and testing over Bluetooth only:

1. **Pairing:** Hold Button `E` for 2s -> pair `Cubelights` in Windows 11 Bluetooth Settings.
2. **Discover & save MAC (and custom PAN IP if changed):**
   ```powershell
   .\.venv\Scripts\cubelights.exe discover
   .\.venv\Scripts\cubelights.exe discover --save-mac AA:BB:CC:DD:EE:FF
   ```
3. **Test PAN transport (`10.55.0.1` or custom `PAN_SUBNET_PREFIX`):**
   - Windows **Devices and Printers -> Cubelights -> Connect using -> Access point**.
   - Verify `ssh pi@10.55.0.1` connects.
   - Verify `.\.venv\Scripts\cubelights.exe status` reports `[via pan-ip:...]`.
   - Run a short Python SDK `FrameStreamer` test to verify 45 FPS live streaming.
4. **Test RFCOMM fallback (disconnect PAN first):**
   - Disconnect the PAN Access Point connection on Windows.
   - Run `.\.venv\Scripts\cubelights.exe status` and `.\.venv\Scripts\cubelights.exe mode wave` -> confirm it reports `[via rfcomm:.../ch2]`.
   - Open the Windows Outgoing Bluetooth COM port (`Cubelights Serial Console`, 115200 baud) in PuTTY / Windows Terminal, press `Enter`, and confirm Linux `login:` prompt works.
5. **Test Tray & Web UI:**
   - Launch `.\.venv\Scripts\cubelights-tray.exe`, test `Ctrl + Alt + B` Busy hotkey, Web UI per-LED painter (`http://127.0.0.1:8765`), pattern upload (`patterns/aurora_cascade.json`), and `Win + L` lock-screen auto-off.

*(Remember to re-enable home Wi-Fi `sudo nmcli radio wifi on` if still updating at home, or leave Wi-Fi unconfigured for office use.)*

---

## 4. OverlayFS & Sudden Power-Loss Verification

1. Enable OverlayFS:
   ```bash
   sudo cubelights-enable-ro
   sudo reboot
   ```
2. Verify protection:
   ```bash
   mount | grep overlay   # Expect overlay on /
   mount | grep /data     # Expect ro on /data
   ```
3. Switch modes / upload a pattern via `cubelights`, wait 5 seconds for the debounced `/data` save, then **unplug power without shutting down**.
4. Plug power back in and verify the Pi boots cleanly with your last saved mode and `/data/patterns/` intact.
5. Once verified, capture your golden SD card backup image following [docs/04-maintenance-and-backups.md](04-maintenance-and-backups.md).
