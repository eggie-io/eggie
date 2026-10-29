"""Windows 11 title bar colours through DWM, matching the page's tokens."""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

# Held equal to the --raised, --ink-2 and --line tokens in desktop/ui/app.css.
COLORS = {
    "light": {"caption": "#fefbf7", "text": "#6a5848", "border": "#efe6d9"},
    "dark": {"caption": "#1c1a33", "text": "#bdb8d8", "border": "#332f52"},
}

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36

_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def colorref(hex_colour: str) -> int:
    # COLORREF is 0x00BBGGRR, the reverse of CSS order.
    h = hex_colour.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply(hwnd: int, dark: bool, set_attribute: Callable[[int, int, int], None]) -> None:
    colors = COLORS["dark" if dark else "light"]
    attributes = (
        (DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0),
        (DWMWA_BORDER_COLOR, colorref(colors["border"])),
        (DWMWA_CAPTION_COLOR, colorref(colors["caption"])),
        (DWMWA_TEXT_COLOR, colorref(colors["text"])),
    )
    for attr, value in attributes:
        # Windows 10 knows only the dark-mode flag; the colour attributes
        # fail there and must not block it.
        try:
            set_attribute(hwnd, attr, value)
        except Exception as e:
            log.warning("title bar attribute %d not applied: %r", attr, e)


def dwm_set_attribute(hwnd: int, attr: int, value: int) -> None:
    import ctypes
    c_value = ctypes.c_int(value)
    hresult = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, attr, ctypes.byref(c_value), ctypes.sizeof(c_value))
    if hresult != 0:
        raise OSError(f"DwmSetWindowAttribute({attr}) failed: HRESULT {hresult & 0xFFFFFFFF:#x}")


def os_uses_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as handle:
            value, _kind = winreg.QueryValueEx(handle, "AppsUseLightTheme")
        return value == 0
    except Exception:
        return False
