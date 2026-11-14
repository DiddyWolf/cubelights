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

echo "[1/7] Checking /boot config path..."
if [[ -f /boot/firmware/config.txt ]]; then
  BOOT_CFG="/boot/firmware/config.txt"
else
  BOOT_CFG="/boot/config.txt"
fi

echo "[2/7] Installing APT system dependencies..."
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

echo "[3/7] Enabling SPI and I2C in ${BOOT_CFG}..."
grep -q '^dtparam=spi=on' "${BOOT_CFG}" || echo 'dtparam=spi=on' >> "${BOOT_CFG}"
grep -q '^dtparam=i2c_arm=on' "${BOOT_CFG}" || echo 'dtparam=i2c_arm=on' >> "${BOOT_CFG}"
if ! grep -q '^i2c-dev$' /etc/modules; then
  echo 'i2c-dev' >> /etc/modules
fi
modprobe spi_bcm2835 || true
modprobe i2c-dev || true

echo "[4/7] Installing cubelights Python packages into /opt/cubelights/venv..."
mkdir -p /opt/cubelights
if [[ ! -d /opt/cubelights/venv ]]; then
  # --system-site-packages exposes apt-installed python3-dbus, python3-gi, python3-spidev, python3-smbus
  # without compiling C extensions on ARMv6.
  python3 -m venv --system-site-packages /opt/cubelights/venv
fi
/opt/cubelights/venv/bin/pip install --no-build-isolation --no-deps "${REPO_ROOT}/shared"
/opt/cubelights/venv/bin/pip install --no-build-isolation --no-deps "${REPO_ROOT}/pi"

echo "[5/7] Setting up /data storage layout..."
mkdir -p /data/patterns /data/shaders
if [[ -d "${REPO_ROOT}/patterns" ]]; then
  cp -n "${REPO_ROOT}/patterns/"*.json /data/patterns/ || true
fi

echo "[6/7] Configuring Bluetooth PAN (pan0), dnsmasq, and BlueZ..."
mkdir -p /etc/systemd/network /etc/NetworkManager/conf.d /etc/dnsmasq.d
cp "${SYS_DIR}/pan0.netdev" /etc/systemd/network/10-pan0.netdev
cp "${SYS_DIR}/pan0.network" /etc/systemd/network/10-pan0.network
cp "${SYS_DIR}/99-unmanaged-pan0.conf" /etc/NetworkManager/conf.d/99-unmanaged-pan0.conf
cp "${SYS_DIR}/dnsmasq-pan0.conf" /etc/dnsmasq.d/cubelights-pan0.conf
cp "${SYS_DIR}/bluetooth-main.conf" /etc/bluetooth/main.conf

systemctl enable systemd-networkd
systemctl restart systemd-networkd || true
systemctl enable dnsmasq
systemctl restart dnsmasq || true
systemctl enable bluetooth
systemctl restart bluetooth || true

echo "[7/7] Installing systemd service and maintenance helper scripts..."
cp "${SYS_DIR}/cubelights.service" /etc/systemd/system/cubelights.service
install -m 0755 "${SYS_DIR}/cubelights-rw" /usr/local/bin/cubelights-rw
install -m 0755 "${SYS_DIR}/cubelights-ro" /usr/local/bin/cubelights-ro
install -m 0755 "${SYS_DIR}/cubelights-enable-ro.sh" /usr/local/bin/cubelights-enable-ro
install -m 0755 "${SYS_DIR}/cubelights-prepare-image.sh" /usr/local/bin/cubelights-prepare-image

systemctl daemon-reload
systemctl enable cubelights.service
systemctl restart cubelights.service

echo ""
echo "Setup complete!"
echo "Next steps:"
echo "  1. Verify LEDs and Button SHIM work cleanly."
echo "  2. Pair Bluetooth with your Windows 11 laptop (hold Button E for 2s)."
echo "  3. Optionally configure a dedicated /data partition in /etc/fstab (see docs/02-pi-provisioning.md)."
echo "  4. Lock down root filesystem with OverlayFS: sudo cubelights-enable-ro"
