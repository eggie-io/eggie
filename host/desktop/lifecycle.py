"""What a launch does, and when autostart turns itself on. Pure."""
from __future__ import annotations

from typing import Callable

from .settings import AUTOSTART_SET_ONCE, Settings

WINDOW = "window"
TRAY_ONLY = "tray_only"


def launch_mode(*, resume: bool, background: bool, vm_exists: Callable[[], bool]) -> str:
    if resume or not background:
        return WINDOW
    try:
        return TRAY_ONLY if vm_exists() else WINDOW
    except Exception:
        return WINDOW


def turn_on_autostart_once(settings: Settings, *, available: bool,
                           enable: Callable[[], None]) -> None:
    if not available or settings.get(AUTOSTART_SET_ONCE):
        return
    try:
        enable()
    except Exception:
        # Still marked: a broken registry must not be retried on every repair.
        pass
    settings.set(AUTOSTART_SET_ONCE, True)
