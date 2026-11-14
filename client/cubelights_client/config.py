"""Client configuration persistence and Windows paired-Bluetooth discovery."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cubelights_shared.protocol import DEFAULT_PAN_IP, DEFAULT_TCP_PORT, DEFAULT_UDP_PORT, RFCOMM_CONTROL_CHANNEL

_BT_ADDR_RE = re.compile(r"([0-9A-Fa-f]{12})")


def default_config_path() -> Path:
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "cubelights" / "client.json"
    return Path.home() / ".config" / "cubelights" / "client.json"


@dataclass(slots=True)
class ClientConfig:
    """User preferences for connecting to the Cubelights Pi."""

    pan_ip: str = DEFAULT_PAN_IP
    tcp_port: int = DEFAULT_TCP_PORT
    udp_port: int = DEFAULT_UDP_PORT
    bt_mac: str | None = None
    rfcomm_channel: int = RFCOMM_CONTROL_CHANNEL
    serial_port: str | None = None
    auto_off_on_lock: bool = True
    busy_hotkey: str = "Ctrl+Alt+B"
    web_port: int = 8765

    def to_dict(self) -> dict[str, Any]:
        return {
            "pan_ip": self.pan_ip,
            "tcp_port": self.tcp_port,
            "udp_port": self.udp_port,
            "bt_mac": self.bt_mac,
            "rfcomm_channel": self.rfcomm_channel,
            "serial_port": self.serial_port,
            "auto_off_on_lock": self.auto_off_on_lock,
            "busy_hotkey": self.busy_hotkey,
            "web_port": self.web_port,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClientConfig:
        return cls(
            pan_ip=str(data.get("pan_ip", DEFAULT_PAN_IP)),
            tcp_port=int(data.get("tcp_port", DEFAULT_TCP_PORT)),
            udp_port=int(data.get("udp_port", DEFAULT_UDP_PORT)),
            bt_mac=data.get("bt_mac"),
            rfcomm_channel=int(data.get("rfcomm_channel", RFCOMM_CONTROL_CHANNEL)),
            serial_port=data.get("serial_port"),
            auto_off_on_lock=bool(data.get("auto_off_on_lock", True)),
            busy_hotkey=str(data.get("busy_hotkey", "Ctrl+Alt+B")),
            web_port=int(data.get("web_port", 8765)),
        )

    @classmethod
    def load(cls, path: Path | None = None) -> ClientConfig:
        target = path or default_config_path()
        if not target.exists():
            return cls()
        with open(target, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def save(self, path: Path | None = None) -> Path:
        target = path or default_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
            fh.write("\n")
        return target


def format_bt_mac(raw_12hex: str) -> str:
    """Format a 12-hex-digit Bluetooth address into colon-separated XX:XX:XX:XX:XX:XX."""
    clean = raw_12hex.replace(":", "").replace("-", "").upper()
    if len(clean) != 12:
        raise ValueError(f"Invalid Bluetooth MAC: {raw_12hex!r}")
    return ":".join(clean[i : i + 2] for i in range(0, 12, 2))


def discover_paired_pis() -> list[dict[str, str]]:
    """Discover paired Bluetooth devices on Windows 11 via PnP device query."""
    if sys.platform != "win32":
        return []
    ps_cmd = (
        "Get-PnpDevice -Class Bluetooth -Status OK | "
        "Select-Object FriendlyName,InstanceId | ConvertTo-Json -Compress"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return []
        raw = json.loads(res.stdout)
        items = raw if isinstance(raw, list) else [raw]
    except Exception:
        return []

    found: list[dict[str, str]] = []
    seen_macs: set[str] = set()
    for item in items:
        name = str(item.get("FriendlyName") or "")
        inst = str(item.get("InstanceId") or "")
        # Standard Bluetooth device instance IDs contain DEV_<12HEX>
        m = re.search(r"DEV_([0-9A-Fa-f]{12})", inst)
        if not m:
            continue
        mac = format_bt_mac(m.group(1))
        if mac in seen_macs:
            continue
        seen_macs.add(mac)
        found.append({"name": name, "mac": mac})
    return found
