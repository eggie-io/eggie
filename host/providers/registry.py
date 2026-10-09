"""HKEY_CURRENT_USER string and binary values, as four injectable callables.

winreg is imported inside each so this module imports on every platform;
the WSL provider's RunOnce resume and the Windows desktop's Run value both
go through here.
"""
from __future__ import annotations


def write_value(key: str, name: str, value: str) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)


def read_value(key: str, name: str) -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
    except FileNotFoundError:
        return None
    return value if isinstance(value, str) else None


def read_binary(key: str, name: str) -> bytes | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
    except FileNotFoundError:
        return None
    return bytes(value) if isinstance(value, (bytes, bytearray)) else None


def delete_value(key: str, name: str) -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.DeleteValue(handle, name)
    except FileNotFoundError:
        pass
