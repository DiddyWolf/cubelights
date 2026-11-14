# 2. Raspberry Pi Provisioning & Read-Only Setup

Perform these steps on your **personal computer at home** where the Pi can reach home Wi-Fi.

## Step 1: Flash Raspberry Pi OS Lite (32-bit)
1. Open **Raspberry Pi Imager**.
2. Choose **Device**: *Raspberry Pi Zero* (or *No filtering*).
3. Choose **OS**: **Raspberry Pi OS Lite (32-bit)** (Debian Trixie — recommended; **Raspberry Pi OS (Legacy, 32-bit) Lite** / Bookworm also works).
   - *Important*: Do **not** select 64-bit or a Desktop ("Full") image; the Pi Zero W v1.1 CPU (ARMv6) is 32-bit only and runs headless.
4. Click **Edit Settings** (OS Customisation):
   - Hostname: `cubelights`
   - Username/Password: e.g. `pi` and a strong password (remember this for SSH and RFCOMM serial console login!)
   - Wireless LAN: enter your **home** Wi-Fi SSID & password
   - Services: **Enable SSH** (add your SSH public key if desired)
5. Write the image to your microSD card (8 GB or larger recommended).

## Step 2: Create the Dedicated `/data` Partition (Recommended Before OverlayFS)
To keep user presets, uploaded patterns, and state persistent across reboots while `/` is locked read-only by OverlayFS, give `/data` its own small partition (`mmcblk0p3`, ~512 MB) on the SD card.

### Option A: Pre-create the Partition on Windows Before First Boot (Recommended)
Right after Raspberry Pi Imager finishes, the SD card has `bootfs` (512 MB), `rootfs` (~2.1 GB), and unallocated space.
On Raspberry Pi OS **Trixie**, `cmdline.txt` on `bootfs` includes the `resize` flag, which normally expands `rootfs` to fill the whole card—**unless** a 3rd partition already exists (in which case the `raspberrypi-sys-mods` resize hook sees `rootfs` is not the last partition and skips expanding it).

To reserve room for `rootfs` (~8 GB total) **and** place `/data` right after it using Windows Disk Management:
1. Open **Disk Management** (`diskmgmt.msc`) on Windows.
2. Right-click the **Unallocated** space -> **New Simple Volume**:
   - Size: `6144` MB (6 GB temporary spacer so `rootfs` has room to grow to ~8 GB)
   - Drive letter: **Do not assign a drive letter**
   - Format: **Do not format this volume**
3. Right-click the remaining **Unallocated** space -> **New Simple Volume**:
   - Size: `512` MB (this will become `mmcblk0p3` for `/data`)
   - Drive letter: **Do not assign a drive letter**
   - Format: **Do not format this volume**
4. Right-click the **6 GB spacer volume** you created in step 2 -> **Delete Volume** (leaving a 6 GB unallocated gap between `rootfs` and `mmcblk0p3`).
   *(Tip: If Disk Management disables "New Simple Volume" on your card reader, use `diskpart`: `select disk N`, `create partition primary size=512 offset=8704000` or create/delete the 6144 MB spacer via `create partition primary size=6144` -> `create partition primary size=512` -> `select partition 3` -> `delete partition`.)*
5. *(Optional)* To prevent any surprise automatic resize during boot, open `bootfs\cmdline.txt` and remove the `resize` token (keep the file on a single line).

### Option B: Boot the Pi, Expand `rootfs` into the Gap, and Format `/data`
1. Boot the Pi and SSH in (`ssh pi@cubelights.local`).
2. Check your disk layout (`lsblk` and `df -h /`).
3. If `mmcblk0p2` is still ~2.1 GB (because `resize` skipped it when `mmcblk0p3` existed), expand `mmcblk0p2` into the 6 GB unallocated gap before `mmcblk0p3`:
   ```bash
   sudo parted /dev/mmcblk0 unit s print free
   # Note the "End" sector of the "Free Space" block right before partition 3, then run:
   sudo parted /dev/mmcblk0 resizepart 2 <END_SECTOR>s
   sudo resize2fs /dev/mmcblk0p2
   ```
   *(If you skipped Option A and let first boot expand `mmcblk0p2` across the entire card, you can shrink `mmcblk0p2` offline with GParted on Linux to make room for `mmcblk0p3`.)*
4. Format and mount `mmcblk0p3` at `/data` (mounted read-only by default):
   ```bash
   sudo mkfs.ext4 -L cubelights-data /dev/mmcblk0p3
   sudo mkdir -p /data
   echo 'LABEL=cubelights-data /data ext4 ro,noatime,nofail 0 2' | sudo tee -a /etc/fstab
   sudo mount /data
   sudo mount -o remount,rw /data
   ```

> [!NOTE]
> If `/data` is not a separate partition, `cubelights-daemon` still works, but once OverlayFS is enabled, any pattern uploads or mode changes saved to `/data` will stay in RAM and reset on reboot.

## Step 3: Copy Repo & Run `setup_pi.sh`
From your home PC, copy the `cubelights` repository to the Pi (e.g. via `scp` or `git clone`), then run:

```bash
cd ~/cubelights
sudo ./pi/system/setup_pi.sh
```

What `setup_pi.sh` installs and configures:
- APT packages: `python3-venv`, `python3-dbus`, `python3-gi`, `python3-spidev`, `python3-smbus`, `bluez`, `dnsmasq`, `parted`, `util-linux`.
- Enables SPI (`dtparam=spi=on`) and I2C (`dtparam=i2c_arm=on`, `i2c-dev`) in `/boot/firmware/config.txt`.
- Installs `cubelights-shared` and `cubelights-pi` into `/opt/cubelights/venv` (with `--system-site-packages` to avoid ARMv6 wheel builds).
- Configures `pan0` bridge (`10.55.0.1/24` via `systemd-networkd` + `dnsmasq` scoped strictly to `pan0`), marks `pan0` unmanaged in NetworkManager, and updates `/etc/bluetooth/main.conf`.
- Installs `cubelights.service` and helper scripts (`cubelights-rw`, `cubelights-ro`, `cubelights-enable-ro`, `cubelights-prepare-image`).

Reboot once after running `setup_pi.sh` so SPI and I2C kernel modules are active:

```bash
sudo reboot
```

## Step 4: Verify Hardware & Calibrate Strip
After reboot, confirm both buses and the running daemon:

```bash
ls /dev/spidev0.*            # Expect /dev/spidev0.0
sudo i2cdetect -y 1          # Expect 0x3f (Button SHIM)
systemctl status cubelights  # Expect active (running)
```

Test Button SHIM buttons `A`–`E`, then calibrate color order (`BGR` vs `RGB`) and segment directions from your laptop or Pi:

```bash
/opt/cubelights/venv/bin/python -c "
from cubelights_shared.protocol import encode_json_command
import socket
s = socket.create_connection(('127.0.0.1', 7777))
s.sendall(encode_json_command('calibrate', duration_s=8.0))
print(s.recv(1024))
"
```

You should see:
- **0–2s**: All LEDs **Red**
- **2–4s**: All LEDs **Green**
- **4–6s**: All LEDs **Blue**
- **6–8s**: `strip_1` Red, `strip_2` Green, `strip_3` Blue (dim -> bright).

If Red and Blue are swapped, set `"color_order": "RGB"` (or run `cubelights color-order RGB`).

## Step 5: Pair Windows 11 Laptop & Enable OverlayFS
1. Follow [docs/03-bluetooth-and-windows.md](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/03-bluetooth-and-windows.md) to pair your Windows 11 work laptop **before** locking OverlayFS (Bluetooth pairing keys live under `/var/lib/bluetooth/` on `/`, so pairing must persist on disk before OverlayFS is enabled!).
2. Once paired and tested (with home Wi-Fi disabled to simulate the cubicle), lock down the root filesystem:
   ```bash
   sudo cubelights-enable-ro
   sudo reboot
   ```
3. Verify OverlayFS is active (`mount | grep overlay` should show `overlay on /`).
4. Capture a golden SD card backup image following [docs/04-maintenance-and-backups.md](file:///c:/Users/DiddyWolf/Projects/cubelights/docs/04-maintenance-and-backups.md).
