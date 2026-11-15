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

## Step 2: Prepare `bootfs` on Windows Before First Boot
Right after Raspberry Pi Imager finishes (re-insert the SD card reader if Windows auto-ejected it), run [prepare_bootfs.ps1](../pi/system/prepare_bootfs.ps1) in PowerShell on your Windows PC:

```powershell
.\pi\system\prepare_bootfs.ps1
```
*(You can pass `-BootDrive D` if auto-detection asks for a drive letter.)*

This script safely updates the FAT32 `bootfs` partition before first boot:
- **`cmdline.txt`**: Removes `resize` (so the Pi does not expand root across the whole SD card) and ensures `console=serial0,115200` is present for PiUART serial console access.
- **`user-data`**: Adds `growpart: { mode: off }` so Trixie's `cloud-init` also leaves the unallocated space free for Step 3.
- **`config.txt`**: Ensures a trailing `[all]` section contains `enable_uart=1`, `dtparam=spi=on`, and `dtparam=i2c_arm=on` (so model filters like `[pi5]` never mask them).

## Step 3: Boot, Get Repo onto the Pi, and Run `setup_pi.sh`
1. Insert the SD card into the Pi Zero W, power on, and wait 2–4 minutes for first-boot `cloud-init` + Wi-Fi setup.
2. Get the repository onto the Pi (replace `cubelights.local` with the Pi's IP if mDNS does not resolve):
   - **Option A — Clone directly from GitHub (recommended):**
     SSH into the Pi (`ssh pi@cubelights.local`) and run:
     ```bash
     command -v git >/dev/null || (sudo apt-get update && sudo apt-get install -y git)
     git clone https://github.com/DiddyWolf/cubelights.git ~/cubelights
     ```
   - **Option B — Copy local unpushed/local commits from Windows PC:**
     From the repo root directory on your Windows PC, archive `main` to a `.tar.gz` file and `scp` it (writing a file avoids Windows PowerShell 5.1 corrupting binary piped streams, and requires no `git` on the Pi):
     ```powershell
     git archive --format=tar.gz -o $env:TEMP\cubelights.tar.gz main
     scp $env:TEMP\cubelights.tar.gz pi@cubelights.local:~/
     ssh pi@cubelights.local "mkdir -p ~/cubelights && tar -xzf ~/cubelights.tar.gz -C ~/cubelights && rm ~/cubelights.tar.gz"
     ```
3. On the Pi, run `setup_pi.sh` from `~/cubelights`:
   ```bash
   cd ~/cubelights
   sudo bash ./pi/system/setup_pi.sh
   ```

What `setup_pi.sh` does automatically:
- **Storage partitions:** Online-resizes `rootfs` (`/dev/mmcblk0p2`) to `8192 MiB` (override via `ROOT_SIZE_MIB`), creates a `512 MiB` `/dev/mmcblk0p3` partition (`DATA_SIZE_MIB`), formats it as `ext4` (`LABEL=cubelights-data`), installs and enables systemd `data.mount` (`ro,noatime`, kept out of `/etc/fstab` so `overlayroot` leaves `/data` persistent on disk), and populates `/data/patterns/` before remounting `/data` `ro`.
- **APT packages:** Installs `python3-venv`, `python3-pip`, `python3-setuptools`, `python3-wheel`, `python3-dbus`, `python3-gi`, `python3-spidev`, `python3-smbus`, `i2c-tools`, `bluez`, `dnsmasq`, `parted`, `util-linux`.
- **SPI & I2C:** Calls `raspi-config nonint do_spi 0` and `do_i2c 0`, and enables `i2c-dev`.
- **Python venv:** Installs `cubelights-shared` and `cubelights-pi` into `/opt/cubelights/venv` (`--system-site-packages` to avoid ARMv6 wheel builds).
- **Bluetooth PAN & Services:** Configures `pan0` bridge (`10.55.0.1/24` via `cubelights-pan0.service` + DHCP-only `dnsmasq` with `port=0`), marks `pan0` unmanaged in NetworkManager (leaving `wlan0` undisturbed), and updates `/etc/bluetooth/main.conf`.
  - *(Avoid corporate `10.x.x.x` collisions)* You can override the `/24` subnet prefix at install time (for example, `172.31.250.0/24` or `192.168.254.0/24`):
    ```bash
    sudo PAN_SUBNET_PREFIX=172.31.250 bash ./pi/system/setup_pi.sh
    ```
    To change it **after** setup (run `sudo cubelights-rw && sudo reboot` first if OverlayFS is already enabled):
    1. Edit `/etc/systemd/system/cubelights-pan0.service` (`172.31.250.1/24`)
    2. Edit `/etc/dnsmasq.d/cubelights-pan0.conf` (`dhcp-range=172.31.250.10,172.31.250.50,255.255.255.0,12h`)
    3. Reload and restart: `sudo systemctl daemon-reload && sudo systemctl restart cubelights-pan0 dnsmasq`
    4. Save the new IP on your Windows laptop: `cubelights discover --save-host 172.31.250.1`
- **Systemd & Helpers:** Installs `cubelights.service` and helper scripts (`cubelights-rw`, `cubelights-ro`, `cubelights-enable-ro`, `cubelights-prepare-image`).

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
1. Follow [docs/03-bluetooth-and-windows.md](03-bluetooth-and-windows.md) to pair your Windows 11 work laptop **before** locking OverlayFS (Bluetooth pairing keys live under `/var/lib/bluetooth/` on `/`, so pairing must persist on disk before OverlayFS is enabled!).
2. Once paired and tested (with home Wi-Fi disabled to simulate the cubicle), lock down the root filesystem:
   ```bash
   sudo cubelights-enable-ro
   sudo reboot
   ```
3. Verify OverlayFS is active (`mount | grep overlay` should show `overlay on /`).
4. Capture a golden SD card backup image following [docs/04-maintenance-and-backups.md](04-maintenance-and-backups.md).
