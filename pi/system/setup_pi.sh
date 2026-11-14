#!/usr/bin/env bash
set -euo pipefail

# Provisioning script for Raspberry Pi Zero W v1.1 running Raspberry Pi OS Lite (32-bit Bookworm).
# Run from the repository root on the Pi via: sudo ./pi/system/setup_pi.sh

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo $0" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SYS_DIR="${REPO_ROOT}/pi/system"
ROOT_SIZE_MIB="${ROOT_SIZE_MIB:-8192}"
DATA_SIZE_MIB="${DATA_SIZE_MIB:-512}"

echo "[1/8] Checking storage partitions (rootfs=${ROOT_SIZE_MIB} MiB, /data=${DATA_SIZE_MIB} MiB)..."
setup_storage_partitions() {
  local root_src disk_name disk_dev p2_start p2_cur p3_start target_p2_sec target_p3_sec disk_sec p3_dev
  root_src="$(findmnt -n -o SOURCE /)"
  disk_name="$(lsblk -no PKNAME "${root_src}" | head -n1)"
  if [[ -z "${disk_name}" ]]; then
    echo "  Skipping partition setup (could not determine parent disk for ${root_src})."
    return 0
  fi
  disk_dev="/dev/${disk_name}"
  if [[ "${disk_name}" == mmcblk* || "${disk_name}" == nvme* ]]; then
    p3_dev="${disk_dev}p3"
  else
    p3_dev="${disk_dev}3"
  fi

  if [[ -b "${p3_dev}" ]]; then
    echo "  Partition ${p3_dev} already exists."
  else
    disk_sec="$(cat "/sys/class/block/${disk_name}/size")"
    p2_start="$(cat "/sys/class/block/${disk_name}p2/start" 2>/dev/null || cat "/sys/class/block/$(basename "${root_src}")/start")"
    p2_cur="$(cat "/sys/class/block/${disk_name}p2/size" 2>/dev/null || cat "/sys/class/block/$(basename "${root_src}")/size")"
    target_p2_sec=$(( ROOT_SIZE_MIB * 2048 ))
    target_p3_sec=$(( DATA_SIZE_MIB * 2048 ))

    if (( p2_cur < target_p2_sec - 2048 )) && (( p2_start + target_p2_sec + target_p3_sec <= disk_sec )); then
      echo "  Resizing ${root_src} to ${ROOT_SIZE_MIB} MiB..."
      echo ",${target_p2_sec}" | sfdisk --no-reread --force -N 2 "${disk_dev}"
      partx -u "${root_src}"
      resize2fs "${root_src}"
      p2_cur="${target_p2_sec}"
    else
      echo "  Keeping existing ${root_src} size ($(( p2_cur / 2048 )) MiB)."
    fi

    # Align p3 start to 1 MiB (2048 sectors) after p2
    p3_start=$(( ((p2_start + p2_cur + 2047) / 2048) * 2048 ))
    if (( p3_start + target_p3_sec <= disk_sec )); then
      echo "  Creating ${p3_dev} (${DATA_SIZE_MIB} MiB) for /data..."
      echo "${p3_start},${target_p3_sec},L" | sfdisk --no-reread --force --append "${disk_dev}"
      partx -a -n 3 "${disk_dev}" || partx -u "${disk_dev}" || true
      udevadm settle >/dev/null 2>&1 || sleep 1
    else
      echo "  WARNING: Not enough unallocated space after ${root_src} to create ${p3_dev}; continuing without dedicated /data partition."
    fi
  fi

  if [[ -b "${p3_dev}" ]]; then
    if ! blkid "${p3_dev}" >/dev/null 2>&1; then
      echo "  Formatting ${p3_dev} as ext4 (LABEL=cubelights-data)..."
      mkfs.ext4 -F -L cubelights-data "${p3_dev}"
    fi
    mkdir -p /data
    if ! grep -q '^LABEL=cubelights-data[[:space:]]\+/data[[:space:]]' /etc/fstab; then
      echo 'LABEL=cubelights-data /data ext4 ro,noatime,nofail 0 2' >> /etc/fstab
    fi
    if ! mountpoint -q /data; then
      mount /data
    fi
    mount -o remount,rw /data
  fi
}

setup_storage_partitions

echo "[2/8] Checking /boot config path..."
if [[ -f /boot/firmware/config.txt ]]; then
  BOOT_CFG="/boot/firmware/config.txt"
else
  BOOT_CFG="/boot/config.txt"
fi

echo "[3/8] Installing APT system dependencies..."
apt-get update
apt-get install -y \
  python3-venv \
  python3-pip \
  python3-setuptools \
  python3-wheel \
  python3-dbus \
  python3-gi \
  python3-spidev \
  python3-smbus \
  i2c-tools \
  bluez \
  dnsmasq \
  parted \
  util-linux

echo "[4/8] Enabling SPI and I2C..."
raspi-config nonint do_spi 0
raspi-config nonint do_i2c 0
if ! grep -q '^i2c-dev$' /etc/modules; then
  echo 'i2c-dev' >> /etc/modules
fi
modprobe spi_bcm2835 || true
modprobe i2c-dev || true

echo "[5/8] Installing cubelights Python packages into /opt/cubelights/venv..."
mkdir -p /opt/cubelights
if [[ ! -d /opt/cubelights/venv ]]; then
  # --system-site-packages exposes apt-installed python3-dbus, python3-gi, python3-spidev, python3-smbus
  # without compiling C extensions on ARMv6.
  python3 -m venv --system-site-packages /opt/cubelights/venv
fi
/opt/cubelights/venv/bin/pip install --no-build-isolation --no-deps "${REPO_ROOT}/shared"
/opt/cubelights/venv/bin/pip install --no-build-isolation --no-deps "${REPO_ROOT}/pi"

echo "[6/8] Setting up /data storage layout..."
mkdir -p /data/patterns /data/shaders
if [[ -d "${REPO_ROOT}/patterns" ]]; then
  cp -n "${REPO_ROOT}/patterns/"*.json /data/patterns/ || true
fi

echo "[7/8] Configuring Bluetooth PAN (pan0), dnsmasq, and BlueZ..."
PAN_SUBNET_PREFIX="${PAN_SUBNET_PREFIX:-10.55.0}"
mkdir -p /etc/NetworkManager/conf.d /etc/dnsmasq.d
cp "${SYS_DIR}/99-unmanaged-pan0.conf" /etc/NetworkManager/conf.d/99-unmanaged-pan0.conf
sed "s/10\\.55\\.0/${PAN_SUBNET_PREFIX}/g" "${SYS_DIR}/cubelights-pan0.service" > /etc/systemd/system/cubelights-pan0.service
sed "s/10\\.55\\.0/${PAN_SUBNET_PREFIX}/g" "${SYS_DIR}/dnsmasq-pan0.conf" > /etc/dnsmasq.d/cubelights-pan0.conf
cp "${SYS_DIR}/bluetooth-main.conf" /etc/bluetooth/main.conf

# Keep NetworkManager in full control of wlan0; do not enable systemd-networkd alongside it.
systemctl daemon-reload
systemctl enable cubelights-pan0.service
systemctl restart cubelights-pan0.service || true
systemctl reload NetworkManager || true
systemctl enable dnsmasq
systemctl restart dnsmasq || true
systemctl enable bluetooth
rfkill unblock bluetooth || /usr/sbin/rfkill unblock bluetooth || true
systemctl restart bluetooth || true

echo "[8/8] Installing systemd service and maintenance helper scripts..."
cp "${SYS_DIR}/cubelights.service" /etc/systemd/system/cubelights.service
install -m 0755 "${SYS_DIR}/cubelights-rw" /usr/local/bin/cubelights-rw
install -m 0755 "${SYS_DIR}/cubelights-ro" /usr/local/bin/cubelights-ro
install -m 0755 "${SYS_DIR}/cubelights-enable-ro.sh" /usr/local/bin/cubelights-enable-ro
install -m 0755 "${SYS_DIR}/cubelights-prepare-image.sh" /usr/local/bin/cubelights-prepare-image

systemctl daemon-reload
systemctl enable cubelights.service
systemctl restart cubelights.service

if mountpoint -q /data; then
  sync
  mount -o remount,ro /data || true
fi

echo ""
echo "Setup complete!"
echo "Next steps:"
echo "  1. Reboot once if SPI/I2C modules weren't already active: sudo reboot"
echo "  2. Verify LEDs and Button SHIM work cleanly."
echo "  3. Pair Bluetooth with your Windows 11 laptop (hold Button E for 2s)."
echo "  4. Lock down root filesystem with OverlayFS: sudo cubelights-enable-ro && sudo reboot"
