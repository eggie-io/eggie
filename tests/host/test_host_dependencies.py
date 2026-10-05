from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Matches the bare distribution name off the front of a PEP 508 requirement
# string, e.g. "uvicorn[standard]>=0.30" -> "uvicorn".
_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")


def _dependency_names() -> set[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    deps = data["project"]["dependencies"]
    names = set()
    for dep in deps:
        match = _NAME_RE.match(dep)
        assert match, f"could not parse dependency name from {dep!r}"
        names.add(match.group(0).lower())
    return names


def test_host_dependencies_exclude_web_framework():
    # The host ships as a PyInstaller-frozen binary; every declared dependency
    # lands in it. fastapi/uvicorn belong to the API's Docker image only.
    names = _dependency_names()
    assert "fastapi" not in names
    assert "uvicorn" not in names


def test_host_declares_no_yaml_parser():
    # Compose files are parsed by the API; host/providers/eggie.yaml is only
    # ever handed to limactl as a path. A declared parser invites the next
    # host-side parse of a file the API owns.
    assert "pyyaml" not in _dependency_names()


def test_webview_backends_are_platform_scoped():
    # pythonnet and pyobjc are native bindings for the *other* platform's
    # backend when installed on the wrong OS -- either a hard install failure
    # or a wasted native toolchain in a bundle that will never load it.
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    deps = data["project"]["dependencies"]

    def find(name: str) -> str:
        dep = next((d for d in deps if d.startswith(name)), None)
        assert dep, f"{name} is not declared"
        return dep

    assert "sys_platform == 'win32'" in find("pythonnet")
    for name in ("pyobjc-core", "pyobjc-framework-Cocoa", "pyobjc-framework-WebKit",
                 "pyobjc-framework-ServiceManagement"):
        assert "sys_platform == 'darwin'" in find(name)


def test_packaging_specs_bundle_the_desktop_ui():
    # host.desktop.__main__.ui_dir() reads host/desktop/ui at runtime; a spec
    # that forgets it ships a window with no HTML to load.
    for spec_path in ("packaging/windows/eggie.spec", "packaging/macos/eggie.spec"):
        spec = (REPO_ROOT / spec_path).read_text()
        assert '"../../host/desktop/ui"' in spec, f"{spec_path} does not bundle host/desktop/ui"


def test_no_host_module_imports_yaml():
    # The dependency assertion above is only half of it: an import that is not
    # declared still works in a dev checkout (the API package installs
    # pyyaml) and only fails in the frozen binary, on a user's machine.
    offenders = []
    for py in sorted((REPO_ROOT / "host").rglob("*.py")):
        for node in ast.walk(ast.parse(py.read_text())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom)
                     and node.level == 0 else [])
            if any(n == "yaml" or n.startswith("yaml.") for n in names):
                offenders.append(f"{py}:{node.lineno}")
    assert not offenders, f"host/ imported yaml: {offenders}"


def test_the_windows_tray_dependencies_are_platform_scoped():
    # pystray's macOS backend needs the main thread pywebview already owns, so
    # the mac tray is native; pystray and Pillow are Windows-only at runtime.
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    deps = data["project"]["dependencies"]
    for name in ("pystray", "Pillow"):
        dep = next((d for d in deps if d.startswith(name)), None)
        assert dep, f"{name} is not declared"
        assert "sys_platform == 'win32'" in dep


def test_packaging_bundles_the_tray_icon_and_uses_it_as_the_app_icon():
    # The tray loads icon.ico at runtime; a spec without it starts a tray
    # with no image and pystray raises on the first draw.
    for spec_path in ("packaging/windows/eggie.spec", "packaging/macos/eggie.spec"):
        spec = (REPO_ROOT / spec_path).read_text()
        assert '"../../host/desktop/resources"' in spec, f"{spec_path} does not bundle resources"
        assert "icon=" in spec, f"{spec_path} does not set the app icon"
    iss = (REPO_ROOT / "packaging/windows/installer.iss").read_text()
    assert "SetupIconFile=" in iss
