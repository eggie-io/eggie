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


def test_every_exe_the_installer_runs_or_stops_is_built():
    text = ISS.read_text()
    referenced = {n.lower() for n in re.findall(r"\{app\}\\([\w.-]+\.exe)", text)}
    # The uninstaller's kill matches by process name, which has no extension.
    referenced |= {f"{n}.exe".lower() for n in re.findall(r"Get-Process\s+([\w.-]+)", text)}
    assert referenced, "installer.iss references no {app} executables"
    assert referenced <= _built(), f"not built by the spec: {referenced - _built()}"


def test_every_exe_the_provider_names_is_built():
    named = {n.lower() for n in re.findall(r'"([\w.-]+\.exe)"', DESKTOP_WIN.read_text())}
    assert named, "desktop_win no longer names its executables"
    assert named <= _built(), f"not built by the spec: {named - _built()}"
