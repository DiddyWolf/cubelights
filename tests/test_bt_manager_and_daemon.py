"""Unit tests for Bluetooth rfkill unblocking and resilient daemon startup."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

from cubelights_pi import bt_manager, daemon


def test_unblock_bluetooth_rfkill_falls_back_to_usr_sbin(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        if cmd[0] == "rfkill":
            raise FileNotFoundError("rfkill not on PATH")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bt_manager.subprocess, "run", fake_run)
    bt_manager._unblock_bluetooth_rfkill()

    assert calls == [
        ["rfkill", "unblock", "bluetooth"],
        ["/usr/sbin/rfkill", "unblock", "bluetooth"],
    ]


def test_daemon_starts_leds_even_if_bluetooth_fails(monkeypatch, tmp_path):
    fake_server = MagicMock()
    started = {"shim": False, "engine": False, "bt_attempted": False}

    class FakeShim:
        def register_callback(self, _cb):
            pass

        def start(self):
            started["shim"] = True

        def stop(self):
            pass

        def set_pixel(self, *_args, **_kwargs):
            pass

    class FakeEngine:
        def __init__(self, config, **_kwargs):
            self.config = config

        def start(self):
            started["engine"] = True

        def stop(self):
            pass

        def set_pairing_indicator(self, *_args, **_kwargs):
            pass

        def set_bluetooth_status(self, *_args, **_kwargs):
            pass

    class FakeBt:
        def start(self):
            started["bt_attempted"] = True
            raise RuntimeError("org.bluez.Error.Failed")

        def stop(self):
            pass

    monkeypatch.setattr(daemon, "MockButtonShimDriver", FakeShim)
    monkeypatch.setattr(daemon, "LedEngine", FakeEngine)
    monkeypatch.setattr(daemon, "BluetoothManager", lambda **_kwargs: FakeBt())
    monkeypatch.setattr(daemon, "CubeLightsServer", lambda **_kwargs: fake_server)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt()))

    try:
        daemon.main(["--mock-hw", "--data-dir", str(tmp_path)])
    except KeyboardInterrupt:
        pass

    assert started == {"shim": True, "engine": True, "bt_attempted": True}


def test_bt_manager_tracks_device_connection_signals():
    states: list[tuple[bool, bool]] = []
    mgr = bt_manager.BluetoothManager(
        on_control_fd=lambda _fd, _lbl: None,
        on_connection_state=lambda ready, conn: states.append((ready, conn)),
    )
    mgr._available = True

    mgr._on_device_properties_changed(
        "org.bluez.Device1",
        {"Connected": True},
        [],
        path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
    )
    mgr._on_device_properties_changed(
        "org.bluez.Device1",
        {"Connected": False},
        [],
        path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
    )
    mgr._on_interfaces_added(
        "/org/bluez/hci0/dev_11_22_33_44_55_66",
        {"org.bluez.Device1": {"Connected": True}},
    )
    mgr._on_interfaces_removed(
        "/org/bluez/hci0/dev_11_22_33_44_55_66",
        ["org.bluez.Device1"],
    )

    assert states == [
        (True, True),
        (True, False),
        (True, True),
        (True, False),
    ]
