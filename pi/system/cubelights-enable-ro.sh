#!/usr/bin/env bash
set -euo pipefail

# Enables OverlayFS read-only root protection via raspi-config.
if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo $0" >&2
  exit 1
fi

if ! mountpoint -q /data; then
  echo "WARNING: /data is NOT a separate mountpoint!"
  echo "If you enable OverlayFS while /data is on the root filesystem,"
  echo "pattern/state saves will live only in RAM and vanish on reboot."
  echo "See docs/02-pi-provisioning.md for creating the /data partition first."
  read -r -p "Continue anyway? [y/N] " reply
  if [[ ! "${reply}" =~ ^[Yy]$ ]]; then
    exit 1
  fi
fi

echo "Enabling OverlayFS via raspi-config..."
raspi-config nonint do_overlayfs 0
echo "OverlayFS enabled. Reboot required to activate read-only root:"
echo "  sudo reboot"
