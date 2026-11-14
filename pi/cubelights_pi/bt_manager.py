"""BlueZ D-Bus Bluetooth manager: PAN (NAP), RFCOMM Serial Console, RFCOMM Control, and Timed Pairing."""

from __future__ import annotations

import logging
import os
import select
import signal
import subprocess
import threading
from typing import Callable

from cubelights_shared.protocol import (
    CUBELIGHTS_CONTROL_UUID,
    RFCOMM_CONSOLE_CHANNEL,
    RFCOMM_CONTROL_CHANNEL,
    SPP_SERIAL_UUID,
)

logger = logging.getLogger(__name__)


def _bridge_rfcomm_pty(rfcomm_fd: int) -> None:
    """Attach a Linux login shell (agetty) over a PTY to an incoming RFCOMM fd."""
    import pty

    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        ["/sbin/agetty", "--noclear", "-", "vt100"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
    )
    os.close(slave_fd)

    try:
        while proc.poll() is None:
            rlist, _, _ = select.select([rfcomm_fd, master_fd], [], [], 1.0)
            if rfcomm_fd in rlist:
                data = os.read(rfcomm_fd, 4096)
                if not data:
                    break
                os.write(master_fd, data)
            if master_fd in rlist:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                os.write(rfcomm_fd, data)
    except OSError as exc:
        logger.debug("RFCOMM console bridge ended: %s", exc)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
        for fd in (master_fd, rfcomm_fd):
            try:
                os.close(fd)
            except OSError:
                pass


class BluetoothManager:
    """Coordinates BlueZ PAN NAP, dual RFCOMM SPP profiles, and timed pairing."""

    def __init__(
        self,
        on_control_fd: Callable[[int, str], None],
        on_pairing_state: Callable[[bool, float], None] | None = None,
        pan_bridge: str = "pan0",
    ) -> None:
        self.on_control_fd = on_control_fd
        self.on_pairing_state = on_pairing_state
        self.pan_bridge = pan_bridge

        self._thread: threading.Thread | None = None
        self._loop = None
        self._bus = None
        self._adapter_props = None
        self._pairing_timer: threading.Timer | None = None
        self._available = False

    def start(self) -> bool:
        """Initialize BlueZ D-Bus registrations on a background GLib main loop."""
        try:
            import dbus  # type: ignore[import-not-found]
            import dbus.mainloop.glib  # type: ignore[import-not-found]
            import dbus.service  # type: ignore[import-not-found]
            from gi.repository import GLib  # type: ignore[import-not-found]
        except ImportError as exc:
            logger.warning("BlueZ D-Bus bindings unavailable (%s); Bluetooth manager disabled.", exc)
            return False

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        self._loop = GLib.MainLoop()

        adapter_path = self._find_adapter_path(dbus)
        if adapter_path is None:
            logger.warning("No Bluetooth HCI adapter found on D-Bus.")
            return False

        adapter_obj = self._bus.get_object("org.bluez", adapter_path)
        self._adapter_props = dbus.Interface(adapter_obj, "org.freedesktop.DBus.Properties")
        self._adapter_props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
        self._adapter_props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(False))
        self._adapter_props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(False))

        # 1. Register PAN NAP server
        try:
            net_srv = dbus.Interface(adapter_obj, "org.bluez.NetworkServer1")
            net_srv.Register("nap", self.pan_bridge)
            logger.info("Registered BlueZ PAN NAP on %s", self.pan_bridge)
        except Exception as exc:
            logger.warning("Could not register PAN NAP (%s): %s", self.pan_bridge, exc)

        # 2. Register pairing agent + RFCOMM profiles
        self._register_agent(dbus)
        self._register_rfcomm_profiles(dbus)

        self._available = True
        self._thread = threading.Thread(target=self._loop.run, name="bluez-glib", daemon=True)
        self._thread.start()
        return True

    def _find_adapter_path(self, dbus) -> str | None:
        mgr = dbus.Interface(
            self._bus.get_object("org.bluez", "/"),
            "org.freedesktop.DBus.ObjectManager",
        )
        objects = mgr.GetManagedObjects()
        for path, ifaces in objects.items():
            if "org.bluez.Adapter1" in ifaces:
                return str(path)
        return None

    def _register_agent(self, dbus) -> None:
        import dbus.service  # type: ignore[import-not-found]

        bus = self._bus

        class PairingAgent(dbus.service.Object):
            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Release(self):
                pass

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
            def RequestAuthorization(self, device):
                self._trust(device)

            @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
            def RequestConfirmation(self, device, passkey):
                logger.info("Auto-confirming SSP pairing for %s (passkey=%06d)", device, passkey)
                self._trust(device)

            @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
            def AuthorizeService(self, device, uuid):
                logger.debug("Authorized service %s for %s", uuid, device)

            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Cancel(self):
                pass

            def _trust(self, device_path: str) -> None:
                try:
                    dev_obj = bus.get_object("org.bluez", device_path)
                    props = dbus.Interface(dev_obj, "org.freedesktop.DBus.Properties")
                    props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True))
                except Exception as exc:
                    logger.warning("Could not mark device %s trusted: %s", device_path, exc)

        agent_path = "/org/cubelights/agent"
        self._agent = PairingAgent(bus, agent_path)
        agent_mgr = dbus.Interface(
            bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.AgentManager1",
        )
        agent_mgr.RegisterAgent(agent_path, "NoInputNoOutput")
        agent_mgr.RequestDefaultAgent(agent_path)
        logger.info("Registered BlueZ pairing agent")

    def _register_rfcomm_profiles(self, dbus) -> None:
        import dbus.service  # type: ignore[import-not-found]

        class RfcommProfile(dbus.service.Object):
            def __init__(self, bus, path: str, handler: Callable[[int, str], None], label: str) -> None:
                super().__init__(bus, path)
                self._handler = handler
                self._label = label

            @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
            def Release(self):
                pass

            @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
            def NewConnection(self, device, fd, fd_properties):
                del fd_properties
                raw_fd = fd.take()
                t = threading.Thread(
                    target=self._handler,
                    args=(raw_fd, f"{self._label}:{device}"),
                    daemon=True,
                )
                t.start()

            @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
            def RequestDisconnection(self, device):
                logger.debug("Disconnection requested for %s (%s)", device, self._label)

        prof_mgr = dbus.Interface(
            self._bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.ProfileManager1",
        )

        # Channel 1: Serial login shell fallback
        console_path = "/org/cubelights/profile/console"
        self._console_profile = RfcommProfile(
            self._bus, console_path, lambda fd, _lbl: _bridge_rfcomm_pty(fd), "rfcomm-console"
        )
        prof_mgr.RegisterProfile(
            console_path,
            SPP_SERIAL_UUID,
            {
                "Name": "Cubelights Serial Console",
                "Role": "server",
                "Channel": dbus.UInt16(RFCOMM_CONSOLE_CHANNEL),
                "RequireAuthentication": dbus.Boolean(True),
                "RequireAuthorization": dbus.Boolean(False),
            },
        )

        # Channel 2: LED control dual-framing stream
        ctrl_path = "/org/cubelights/profile/control"
        self._control_profile = RfcommProfile(
            self._bus, ctrl_path, self.on_control_fd, "rfcomm-control"
        )
        prof_mgr.RegisterProfile(
            ctrl_path,
            CUBELIGHTS_CONTROL_UUID,
            {
                "Name": "Cubelights Control",
                "Role": "server",
                "Channel": dbus.UInt16(RFCOMM_CONTROL_CHANNEL),
                "RequireAuthentication": dbus.Boolean(True),
                "RequireAuthorization": dbus.Boolean(False),
            },
        )
        logger.info(
            "Registered RFCOMM profiles: Console ch=%d, Control ch=%d",
            RFCOMM_CONSOLE_CHANNEL,
            RFCOMM_CONTROL_CHANNEL,
        )

    def enter_pairing_mode(self, duration_s: float = 120.0) -> None:
        """Open a timed Discoverable+Pairable window for pairing the work laptop."""
        if self.on_pairing_state is not None:
            self.on_pairing_state(True, duration_s)

        if self._pairing_timer is not None:
            self._pairing_timer.cancel()

        if self._available and self._adapter_props is not None:
            import dbus  # type: ignore[import-not-found]

            self._adapter_props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(int(duration_s)))
            self._adapter_props.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(int(duration_s)))
            self._adapter_props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(True))
            self._adapter_props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(True))
            logger.info("Entered Bluetooth pairing mode for %.0fs", duration_s)

        timer = threading.Timer(duration_s, self.exit_pairing_mode)
        timer.daemon = True
        self._pairing_timer = timer
        timer.start()

    def exit_pairing_mode(self) -> None:
        if self._available and self._adapter_props is not None:
            try:
                import dbus  # type: ignore[import-not-found]

                self._adapter_props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(False))
                self._adapter_props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(False))
            except Exception:
                pass
        if self.on_pairing_state is not None:
            self.on_pairing_state(False, 0.0)
        logger.info("Exited Bluetooth pairing mode")

    def stop(self) -> None:
        if self._pairing_timer is not None:
            self._pairing_timer.cancel()
        if self._loop is not None:
            try:
                self._loop.quit()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.5)
