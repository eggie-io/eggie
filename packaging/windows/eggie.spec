# PyInstaller one-dir. One-file unpacks to a temp dir on every launch and is
# the mode antivirus heuristics dislike most; the installer wraps this anyway.

# Spec files are exec'd with PyInstaller's own namespace, which does not
# include the standard library.
import os
import re

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo)

VERSION = os.environ.get("EGGIE_VERSION", "0.0.0")

# host.desktop is reached only through cli.setup()'s function-local import,
# so PyInstaller's static analysis never sees it -- a bundle missing one of
# these launches, shows a window, and dies on the first draw. webview and its
# platform backend are the same problem one layer down: pywebview picks its
# backend at runtime (`import webview.platforms.edgechromium` inside the
# library, not a top-level import of ours), so PyInstaller's analysis never
# sees that either. edgechromium is pywebview's Windows backend (WebView2 via
# pythonnet) -- unverified against an installed pywebview, since none is
# installed in this environment; confirm against the real package before a
# frozen build.
HIDDEN = ["host.desktop.__main__", "host.desktop.api", "host.desktop.view",
          "host.desktop.jobs", "host.desktop.controller", "host.desktop.lifecycle",
          "host.desktop.settings", "host.providers.tray_win",
          "host.providers.instance", "pystray._win32", "PIL.Image",
          "webview", "webview.platforms.edgechromium"]

a = Analysis(
    ["../../host/cli.py"],
    pathex=["../.."],
    datas=[
        ("../../host/provision/nginx-hello/docker-compose.yml",
         "host/provision/nginx-hello"),
        ("../../host/providers/eggie.yaml", "host/providers"),
        ("../../host/desktop/ui", "host/desktop/ui"),
        # The tray loads icon.ico at runtime.
        ("../../host/desktop/resources", "host/desktop/resources"),
    ],
    # Nothing from runtime/ is bundled: the VM pulls the API image and fetches
    # the rest of the runtime itself, and tests/host/test_frozen_bundle.py
    # fails if an entry reappears. Every dest mirrors the repo path its reader
    # resolves from __file__, so the bundle and a source checkout look identical.
    hiddenimports=HIDDEN,
)
pyz = PYZ(a.pure)



def _version_tuple(version):
    # Leading digits only, so a "0.3.0rc1" build still stamps 0.3.0.0.
    parts = [int(re.match(r"\d*", p).group() or 0) for p in version.split(".")[:4]]
    return tuple(parts + [0] * (4 - len(parts)))


def _version_info(filename):
    # Task Manager and Alt-Tab show FileDescription; without it they fall back
    # to the file name.
    numbers = _version_tuple(VERSION)
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Eggie"),
                StringStruct("FileDescription", "Eggie"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", filename),
                StringStruct("OriginalFilename", f"{filename}.exe"),
                StringStruct("ProductName", "Eggie"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


# Two executables, one Analysis, one entry point. A GUI-subsystem exe has no
# console, so anything typer echoes from it is discarded and PowerShell does
# not wait on it ($LASTEXITCODE would be stale). The CLI therefore must be
# console; the app window must not be, or it flashes a console behind itself.
cli_exe = EXE(pyz, a.scripts, exclude_binaries=True, name="eggie", console=True,
              icon="../../host/desktop/resources/icon.ico",
              version=_version_info("eggie"))
# Not "Eggie": Windows paths are case-insensitive, so that would be eggie.exe.
app_exe = EXE(pyz, a.scripts, exclude_binaries=True, name="eggie-app", console=False,
              icon="../../host/desktop/resources/icon.ico",
              version=_version_info("eggie-app"))

coll = COLLECT(cli_exe, app_exe, a.binaries, a.datas, name="Eggie")
