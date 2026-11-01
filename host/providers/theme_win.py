"""Windows light/dark settings, read now and watched for changes."""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)

PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def _read(name: str) -> int | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
        return value
    except Exception:
        return None


def apps_use_dark() -> bool:
    return _read("AppsUseLightTheme") == 0


def taskbar_is_light() -> bool:
    # Windows before 1903 has no value and a dark taskbar.
    return _read("SystemUsesLightTheme") == 1


def wait_for_personalize_change() -> Callable[[], None]:
    """Returns a call that blocks until a value under the key changes."""
    import ctypes
    import winreg

    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY, 0, winreg.KEY_NOTIFY)
    REG_NOTIFY_CHANGE_LAST_SET = 0x4

    def wait() -> None:
        status = ctypes.windll.advapi32.RegNotifyChangeKeyValue(
            int(key), False, REG_NOTIFY_CHANGE_LAST_SET, None, False)
        if status != 0:
            raise OSError(f"RegNotifyChangeKeyValue failed: {status}")
    return wait


class ThemeWatch:
    """Calls every subscriber after each change to the theme settings.

    The thread starts on the first subscribe, so a desktop that never shows a
    window or a tray never opens the key.
    """

    def __init__(self, open_waiter: Callable[[], Callable[[], None]] = wait_for_personalize_change):
        self._open_waiter = open_waiter
        self._subscribers: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def subscribe(self, on_change: Callable[[], None]) -> None:
        with self._lock:
            self._subscribers.append(on_change)
            if self._thread is None:
                self._thread = threading.Thread(target=self.run, daemon=True, name="eggie-theme")
                self._thread.start()

    def run(self) -> None:
        try:
            wait = self._open_waiter()
            while True:
                wait()
                with self._lock:
                    subscribers = list(self._subscribers)
                for on_change in subscribers:
                    try:
                        on_change()
                    except Exception:
                        log.exception("theme change handler failed")
        except Exception as e:
            log.warning("stopped watching the Windows theme: %r", e)
