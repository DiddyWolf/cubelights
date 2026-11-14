# 4. Maintenance, OverlayFS Updates & SD Card Backups

## How the Filesystem Is Protected
To prevent SD card corruption when cubicle power is unplugged without shutting down:

| Mount | Normal Runtime State | What Lives Here | Persists Across Reboot? |
| --- | --- | --- | --- |
| `/` (root) | **Read-only via `OverlayFS`** (writes go to RAM `tmpfs`) | OS, `cubelights` code, systemd units, `/var/lib/bluetooth` | **No** (unless OverlayFS is temporarily disabled) |
| `/data` | **Mounted `ro`**, transiently remounted `rw` during atomic save | `config.json`, `state.json`, `patterns/*.json`, `shaders/*.py` | **Yes** |
| `/boot/firmware` | Mounted normally (rarely touched) | `config.txt`, kernel/cmdline | Yes (only edit while maintaining) |

---

## Performing Maintenance & Code/OS Updates

Because `/` uses `OverlayFS`, any `apt upgrade`, `pip install`, `bluetoothctl` pairing, or systemd edits made while OverlayFS is active **disappear on reboot**. Follow this exact workflow for permanent OS or code updates:

1. **Connect** to the Pi via SSH (`ssh pi@10.55.0.1` over PAN, or home Wi-Fi, or RFCOMM Serial Console).
2. **Disable OverlayFS and reboot into writable mode**:
   ```bash
   sudo cubelights-rw
   sudo reboot
   ```
3. **Perform your updates** (after reboot, `mount | grep overlay` returns nothing):
   - **Update `cubelights` code**:
     ```bash
     cd ~/cubelights
     git pull   # or scp updated files over PAN/Wi-Fi
     sudo /opt/cubelights/venv/bin/pip install --no-build-isolation --no-deps ./shared ./pi
     sudo systemctl restart cubelights
     ```
   - **Update OS packages**:
     ```bash
     sudo apt-get update && sudo apt-get upgrade -y
     ```
   - **Re-pair or add a new Bluetooth device**: Pair while in writable mode so `/var/lib/bluetooth/` is saved on disk!
4. **Re-enable OverlayFS and reboot back into protected mode**:
   ```bash
   sudo cubelights-ro
   sudo reboot
   ```
5. Confirm protection is active:
   ```bash
   mount | grep overlay
   # Expected: overlayroot on / type overlay (...)
   ```

---

## Editing `/data` Configs or Custom Shaders Manually

You do **not** need to disable OverlayFS to edit `/data/config.json`, `/data/patterns/`, or `/data/shaders/`!
- When editing via the laptop client (`cubelights upload`, `set_segment_map`, `set_color_order`), `cubelights-daemon` handles the `ro -> rw -> atomic write -> ro` cycle automatically.
- If editing files on `/data` manually over SSH:
  ```bash
  sudo mount -o remount,rw /data
  sudo nano /data/config.json
  sync
  sudo mount -o remount,ro /data
  sudo systemctl restart cubelights
  ```

---

## Creating & Restoring a Golden SD Card Backup Image

Once the Pi is fully configured, paired with your work laptop, tested, and ready for your cubicle, take a complete SD card backup image so you can restore a corrupted or lost SD card in minutes.

### Creating the Backup Image
1. Put the Pi into writable mode (`sudo cubelights-rw && sudo reboot`) if you want to clean logs/histories first.
2. Run the image-prep helper:
   ```bash
   sudo cubelights-prepare-image
   ```
   - This stops `cubelights.service`, vacuums old journals, cleans apt caches, clears bash histories, and re-enables OverlayFS so the captured image boots straight into protected read-only mode.
   - *(Optional)* If you do **not** want your home Wi-Fi credentials stored inside the backup image, remove the home connection profile before shutting down:
     ```bash
     sudo rm -f /etc/NetworkManager/system-connections/*.nmconnection
     ```
3. Shut down cleanly:
   ```bash
   sudo poweroff
   ```
4. Remove the microSD card and insert it into your **personal computer**.
5. Capture a raw `.img` backup:
   - **Windows**: Use **Win32 Disk Imager** (`Read` to `cubelights-golden-YYYYMMDD.img`), then compress with 7-Zip / `gzip` / `xz`.
   - **Linux / macOS**:
     ```bash
     sudo dd if=/dev/sdX bs=4M status=progress | xz -T0 -z > cubelights-golden.img.xz
     ```
   - *(Tip)* Keep a copy of `cubelights-golden.img.xz` backed up on your personal PC or cloud storage (and avoid plugging USB drives into your restricted work laptop).

### Restoring from the Golden Image
1. Open **Raspberry Pi Imager** on your personal PC.
2. Under **Operating System**, select **Use custom** and pick your `.img` / `.img.xz` file.
3. Flash to a fresh microSD card (same capacity or larger), insert into the Pi Zero W, and power on. It boots directly with your exact config, Bluetooth pairing keys, `/data` patterns, and OverlayFS protection intact!

---

## Troubleshooting & Adafruit PiUART Recovery

### Checking Logs & Hardware
```bash
sudo journalctl -u cubelights -n 100 --no-pager
sudo journalctl -u bluetooth -n 50 --no-pager
ip addr show pan0
bluetoothctl show
```

### Emergency Serial Console via Adafruit PiUART
If Bluetooth and Wi-Fi are both unreachable (e.g., after a misconfigured network edit):
1. Ensure UART is enabled in `/boot/firmware/config.txt` (`enable_uart=1`, or add it from your personal PC by editing `/boot/firmware/config.txt` on the SD card's FAT32 boot partition!).
2. Connect the Adafruit PiUART to the Pi's GPIO header (`GND`, `TX -> GPIO15 RXD`, `RX -> GPIO14 TXD`; do **not** double-power 5V if PiPixel is already supplying 5V!).
3. Open the PiUART COM port at **115200 baud, 8N1** in PuTTY / Windows Terminal.
4. Log in, run `sudo cubelights-rw && sudo reboot` if OS files need repair, or restore from your golden backup image.
