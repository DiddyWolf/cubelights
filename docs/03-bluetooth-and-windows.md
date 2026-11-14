# 3. Bluetooth Setup & Windows 11 Client Guide

## Bluetooth Architecture Summary
The Pi exposes three services over Bluetooth (paired & trusted, non-discoverable by default):
1. **Bluetooth PAN (NAP)** (`10.55.0.1/24`): Full IP link for `ssh`, `scp`, and fast TCP/UDP LED streaming.
2. **RFCOMM Channel 1 (`Cubelights Serial Console`)**: Standard Bluetooth Serial Port Profile (SPP) mapped to a Linux login shell—works even when a corporate VPN or policy blocks Bluetooth PAN.
3. **RFCOMM Channel 2 (`Cubelights Control`)**: Dedicated Bluetooth stream speaking the same JSON + binary frame protocol as TCP `7777`.

## Pairing the Windows 11 Work Laptop
> [!IMPORTANT]
> Pair your work laptop **while OverlayFS is disabled** (`cubelights-rw`), so the Bluetooth link keys in `/var/lib/bluetooth/` are permanently written to the SD card before re-enabling OverlayFS.

1. On the Pi, **hold Button `E` for 2 seconds** (the Button SHIM LED pulses blue for 120 seconds).
2. On Windows 11: **Settings -> Bluetooth & devices -> Add device -> Bluetooth**.
3. Select **Cubelights** (or `cubelights`) and confirm pairing.
4. Once paired, Windows automatically configures Bluetooth PAN and virtual Bluetooth COM ports.

## Installing the Windows 11 Client
In PowerShell on your work laptop (from the `cubelights` repo):

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -e .\shared -e .\client
```

Discover and save the Pi's Bluetooth MAC address so RFCOMM auto-fallback knows which device to connect to:

```powershell
.\.venv\Scripts\cubelights.exe discover
.\.venv\Scripts\cubelights.exe discover --save-mac AA:BB:CC:DD:EE:FF
```

## Connecting at Your Cubicle

### Option A: Bluetooth PAN (SSH + Fast Control)
1. In Windows 11, open **Control Panel -> Devices and Printers** (or search **View devices and printers**), right-click **Cubelights -> Connect using -> Access point**.
2. Once connected, your laptop gets an IP in `10.55.0.10–50`.
3. SSH works natively:
   ```powershell
   ssh pi@10.55.0.1
   ```
4. `cubelights` CLI/Tray automatically uses `10.55.0.1:7777` (TCP) and `7778` (UDP frames).

### Option B: RFCOMM Fallback (No PAN / Strict Corporate VPN)
If your corporate VPN blocks PAN or you didn't connect the PAN access point:
- **LED Control**: `cubelights` automatically detects PAN is unreachable (~400 ms timeout) and connects directly over **Bluetooth RFCOMM Channel 2** using your saved `bt_mac` (`socket.AF_BLUETOOTH`).
- **Maintenance Console**: Check Windows **Bluetooth Settings -> More Bluetooth options -> COM Ports** for the **Outgoing** port assigned to `Cubelights Serial Console` (e.g., `COM5`). Open `COM5` at `115200` baud in **PuTTY** or **Windows Terminal**, press `Enter`, and log in with your Pi username/password.

## Using the Client Tools

### System Tray + Local Web UI
```powershell
.\.venv\Scripts\cubelights-tray.exe
```
- Opens **http://127.0.0.1:8765/** with a live 29-LED segment diagram, mode/seasonal selector, brightness slider, per-LED color painter, and JSON keyframe uploader.
- **Global Hotkey**: Press `Ctrl + Alt + B` from any Windows app to toggle **Busy / On-Call** pulsing red.
- **Lock-Screen Auto-Off**: Locking Windows (`Win + L`) automatically turns the cubicle lights off; unlocking restores them (`auto_off_on_lock` in `%APPDATA%\cubelights\client.json`).

### CLI Examples
```powershell
cubelights status
cubelights busy on
cubelights busy off
cubelights mode rainbow --params '{"speed": 0.05}'
cubelights mode wave
cubelights mode seasonal --params '{"theme": "auto"}'
cubelights brightness 40
cubelights set-zone strip_2 "#0088FF"
cubelights set-led 0 "#FF0000"
cubelights upload .\patterns\aurora_cascade.json
```
