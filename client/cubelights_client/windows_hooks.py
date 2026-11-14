"""Windows 11 session lock/unlock and global hotkey hooks using ctypes."""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

logger = logging.getLogger(__name__)

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_WM_HOTKEY = 0x0312
_WM_WTSSESSION_CHANGE = 0x02B1
_WM_QUIT = 0x0012
_WTS_SESSION_LOCK = 0x7
_WTS_SESSION_UNLOCK = 0x8
_NOTIFY_FOR_THIS_SESSION = 0x0


def parse_hotkey(hotkey: str) -> tuple[int, int]:
    """Parse hotkey strings like 'Ctrl+Alt+B' into (modifiers, vk_code)."""
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    mods = 0
    key_part = None
    for part in parts:
        if part in ("ctrl", "control"):
            mods |= _MOD_CONTROL
        elif part == "alt":
            mods |= _MOD_ALT
        elif part == "shift":
            mods |= _MOD_SHIFT
        elif part in ("win", "super"):
            mods |= _MOD_WIN
        else:
            key_part = part

    if not key_part or len(key_part) != 1:
        raise ValueError(f"Unsupported hotkey specification: {hotkey!r}")
    vk = ord(key_part.upper())
    return mods, vk


class WindowsHookRunner:
    """Runs a hidden message-only Win32 window for lock/unlock and global hotkeys."""

    def __init__(
        self,
        on_lock: Callable[[], None] | None = None,
        on_unlock: Callable[[], None] | None = None,
        hotkey: str | None = "Ctrl+Alt+B",
        on_hotkey: Callable[[], None] | None = None,
    ) -> None:
        self.on_lock = on_lock
        self.on_unlock = on_unlock
        self.hotkey = hotkey
        self.on_hotkey = on_hotkey
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._stop_evt = threading.Event()

    def start(self) -> bool:
        if sys.platform != "win32":
            logger.debug("Windows hooks skipped on non-Windows platform %s", sys.platform)
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run_win32_loop, name="win32-hooks", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_evt.set()
        if sys.platform == "win32" and self._thread_id is not None:
            try:
                import ctypes

                ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.5)

    def _run_win32_loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        wtsapi32 = ctypes.windll.wtsapi32

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        self._thread_id = kernel32.GetCurrentThreadId()

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == _WM_WTSSESSION_CHANGE:
                if wparam == _WTS_SESSION_LOCK and self.on_lock:
                    threading.Thread(target=self.on_lock, daemon=True).start()
                elif wparam == _WTS_SESSION_UNLOCK and self.on_unlock:
                    threading.Thread(target=self.on_unlock, daemon=True).start()
                return 0
            if msg == _WM_HOTKEY and self.on_hotkey:
                threading.Thread(target=self.on_hotkey, daemon=True).start()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc_ref = WNDPROC(wnd_proc)
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = f"CubeLightsHookWnd_{self._thread_id}"
        wc = WNDCLASSW()
        wc.lpfnWndProc = proc_ref
        wc.hInstance = hinst
        wc.lpszClassName = cls_name

        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            logger.warning("RegisterClassW failed for Windows hooks")
            return

        HWND_MESSAGE = wintypes.HWND(-3)
        hwnd = user32.CreateWindowExW(
            0, cls_name, "CubeLightsHooks", 0, 0, 0, 0, 0, HWND_MESSAGE, None, hinst, None
        )
        if not hwnd:
            logger.warning("CreateWindowExW failed for Windows hooks")
            return

        wts_ok = False
        if self.on_lock or self.on_unlock:
            wts_ok = bool(wtsapi32.WTSRegisterSessionNotification(hwnd, _NOTIFY_FOR_THIS_SESSION))

        hotkey_id = 1
        hotkey_ok = False
        if self.hotkey and self.on_hotkey:
            try:
                mods, vk = parse_hotkey(self.hotkey)
                hotkey_ok = bool(user32.RegisterHotKey(hwnd, hotkey_id, mods, vk))
            except Exception as exc:
                logger.warning("Could not register hotkey %r: %s", self.hotkey, exc)

        msg = wintypes.MSG()
        try:
            while not self._stop_evt.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if hotkey_ok:
                user32.UnregisterHotKey(hwnd, hotkey_id)
            if wts_ok:
                wtsapi32.WTSUnRegisterSessionNotification(hwnd)
            user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(cls_name, hinst)
