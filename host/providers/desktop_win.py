"""What the desktop app asks of Windows: the tray, one instance per user,
open at login, and letting sign-out close the window.

Nothing here touches the VM; that is Wsl2Provider's job.
"""
from __future__ import annotations

import getpass
from pathlib import Path

from . import registry

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Task Manager's Startup tab leaves the Run value alone and records its own
# switch here: first byte 0x03 = disabled; missing or anything else = enabled.
STARTUP_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
_STARTUP_DISABLED = 0x03
AUTOSTART_VALUE_NAME = "Eggie"


def _allow_any_foreground() -> None:
    # Windows only lets the process the user just launched take the
    # foreground; without this the first instance's window opens behind.
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    except Exception:
        pass


def run_value(exe_path: str) -> str:
    return f'"{exe_path}" setup --background'


def _gui_exe(exe_path: str) -> str:
    # eggie.exe is the console build; at login it would flash a window.
    path = Path(exe_path)
    if path.name.lower() == "eggie.exe":
        sibling = path.with_name("setup.exe")
        if sibling.exists():
            return str(sibling)
    return exe_path


class WindowsDesktop:
    def __init__(self, registry_writer=registry.write_value,
                 registry_reader=registry.read_value,
                 registry_deleter=registry.delete_value,
                 registry_binary_reader=registry.read_binary):
        self._write_registry = registry_writer
        self._read_registry = registry_reader
        self._delete_registry = registry_deleter
        self._read_registry_binary = registry_binary_reader

    def watch_login_launch(self, on_login) -> None:
        """Nothing to watch: the Run value passes --background itself."""

    def on_window_shown(self, visible: bool) -> None:
        """The taskbar button follows the window on its own."""

    def let_session_end_close(self, on_session_end) -> None:
        from .tray_win import let_session_end_close
        let_session_end_close(on_session_end)

    def tray(self, *, icon, on_open, on_settings, on_quit):
        from .tray_win import WinTray
        return WinTray(icon=icon, on_open=on_open, on_settings=on_settings, on_quit=on_quit)

    def single_instance(self, on_show, *, announce: bool) -> bool:
        from . import instance
        try:
            user = getpass.getuser()
        except Exception:
            user = "user"
        # Pipe names are machine-wide: without the user name, another user's
        # Eggie would answer and show its window instead.
        address = rf"\\.\pipe\eggie-{user}"
        return instance.claim(address, "AF_PIPE", on_show, announce=announce,
                              before_show=_allow_any_foreground)

    def autostart_enabled(self, exe_path: str) -> bool:
        if self._read_registry(RUN_KEY, AUTOSTART_VALUE_NAME) != run_value(_gui_exe(exe_path)):
            return False
        approved = self._read_registry_binary(STARTUP_APPROVED_KEY, AUTOSTART_VALUE_NAME)
        return not (approved and approved[0] == _STARTUP_DISABLED)

    def set_autostart(self, on: bool, exe_path: str) -> None:
        if on:
            self._write_registry(RUN_KEY, AUTOSTART_VALUE_NAME,
                                 run_value(_gui_exe(exe_path)))
            self._delete_registry(STARTUP_APPROVED_KEY, AUTOSTART_VALUE_NAME)
        else:
            self._delete_registry(RUN_KEY, AUTOSTART_VALUE_NAME)
