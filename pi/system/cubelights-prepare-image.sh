#!/usr/bin/env bash
set -euo pipefail

# Prepares the Pi before shutting down to take a golden SD card backup image.
# Run this with OverlayFS disabled (after `sudo cubelights-rw && sudo reboot`).

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo $0" >&2
  exit 1
fi

echo "Stopping cubelights service..."
systemctl stop cubelights.service || true

echo "Vacuuming systemd journal and apt caches..."
journalctl --rotate || true
journalctl --vacuum-time=1d || true
apt-get clean

echo "Clearing bash histories..."
rm -f /root/.bash_history /home/*/.bash_history || true

read -r -p "Re-enable OverlayFS before shutdown so the backup image boots read-only? [Y/n] " reply
if [[ ! "${reply}" =~ ^[Nn]$ ]]; then
  raspi-config nonint do_overlayfs 0
fi

sync
echo "Golden image prep complete. Run 'sudo poweroff', remove SD card, and capture image."
