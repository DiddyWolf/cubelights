"""Windows 11 laptop client SDK, CLI, system tray, and Web UI for Cubelights."""

from .config import ClientConfig, discover_paired_pis
from .sdk import CubeLightsClient, FrameStreamer
from .transport import TransportError

__all__ = [
    "ClientConfig",
    "CubeLightsClient",
    "FrameStreamer",
    "TransportError",
    "discover_paired_pis",
]
