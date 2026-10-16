"""The installer and the provider name executables the spec must build.

A rename that misses one of them still builds and installs cleanly; the
failure is a shortcut or a login entry pointing at a file that isn't there.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "packaging" / "windows" / "eggie.spec"
ISS = ROOT / "packaging" / "windows" / "installer.iss"
DESKTOP_WIN = ROOT / "host" / "providers" / "desktop_win.py"


def _built() -> set[str]:
    names = re.findall(r'EXE\(.*?name="([^"]+)"', SPEC.read_text(), re.S)
    assert names, f"no EXE names found in {SPEC}"
    return {f"{n}.exe".lower() for n in names}


def test_every_exe_the_installer_runs_is_built():
    referenced = {n.lower() for n in re.findall(r"\{app\}\\([\w.-]+\.exe)", ISS.read_text())}
    assert referenced, "installer.iss references no {app} executables"
    assert referenced <= _built(), f"not built by the spec: {referenced - _built()}"


def test_the_gui_sibling_the_provider_looks_for_is_built():
    sibling = re.search(r'with_name\("([^"]+)"\)', DESKTOP_WIN.read_text())
    assert sibling, "desktop_win no longer names a GUI sibling"
    assert sibling.group(1).lower() in _built()


def test_start_menu_shortcuts_are_named_eggie():
    icons = re.findall(r'^Name:\s*"\{group\}\\([^"]+)"', ISS.read_text(), re.M)
    assert icons, "installer.iss creates no Start menu shortcut"
    assert icons == ["Eggie"]
