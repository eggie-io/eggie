# PyInstaller one-dir, wrapped in an .app bundle. The Windows twin builds two
# executables from one Analysis because the difference there is only the
# subsystem; here they differ in their entry script — the bundle's main
# executable has to default to the setup command, since Finder passes no
# arguments (see setup_main.py).

# Spec files are exec'd with PyInstaller's own namespace, which does not
# include the standard library.
import os

DATAS = [
    ("../../host/provision/nginx-hello/docker-compose.yml",
     "host/provision/nginx-hello"),
    ("../../host/providers/eggie.yaml", "host/providers"),
    ("../../host/desktop/ui", "host/desktop/ui"),
    # The tray loads icon.ico at runtime.
    ("../../host/desktop/resources", "host/desktop/resources"),
]
# Nothing from runtime/ is bundled: the VM pulls the API image and fetches the
# rest of the runtime itself, and tests/host/test_frozen_bundle.py fails if an
# entry reappears. Every dest mirrors the repo path its reader resolves from
# __file__, so the bundle and a source checkout look identical.

# host.desktop is reached only through cli.setup()'s function-local import,
# so PyInstaller's static analysis never sees it -- a bundle missing one of
# these launches, shows a Dock icon, and dies on the first draw. webview and
# its platform backend are the same problem one layer down: pywebview picks
# its backend at runtime (`import webview.platforms.cocoa` inside the
# library, not a top-level import of ours), so PyInstaller's analysis never
# sees that either. cocoa is pywebview's macOS backend (WKWebView via pyobjc)
# -- unverified against an installed pywebview, since none is installed in
# this environment; confirm against the real package before a frozen build.
HIDDEN = ["host.desktop.__main__", "host.desktop.api", "host.desktop.view",
          "host.desktop.jobs", "host.desktop.controller", "host.desktop.lifecycle",
          "host.desktop.settings", "host.providers.tray_mac",
          "host.providers.mac_login", "ServiceManagement", "PyObjCTools.AppHelper",
          "webview", "webview.platforms.cocoa"]

cli = Analysis(["../../host/cli.py"], pathex=["../.."], datas=DATAS,
               hiddenimports=HIDDEN)
gui = Analysis(["setup_main.py"], pathex=["../.."], datas=DATAS,
               hiddenimports=HIDDEN)

# The GUI executable comes first on purpose: BUNDLE takes CFBundleExecutable
# from the first EXECUTABLE in the COLLECT, and double-clicking Eggie.app must
# open the setup window, not a CLI with nothing to do. `eggie` lands beside it
# in Contents/MacOS, which is what the installer symlinks onto PATH.
#
# The name is not "Eggie": both executables share one directory, and a Mac
# filesystem is case-insensitive by default, so `Eggie` and `eggie` are one
# file. COLLECT wrote them in order and the second silently replaced the first
# -- the built app launched the CLI, printed its help to a console nobody was
# watching, and quit. Nothing about the build said so.
setup_exe = EXE(PYZ(gui.pure), gui.scripts, exclude_binaries=True,
                name="eggie-setup", console=False)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, exclude_binaries=True,
              name="eggie", console=True)

coll = COLLECT(setup_exe, cli_exe,
               gui.binaries, gui.datas, cli.binaries, cli.datas,
               name="Eggie")

app = BUNDLE(
    coll,
    name="Eggie.app",
    # PyInstaller converts the .ico to .icns through Pillow (the dev extra).
    icon="../../host/desktop/resources/icon.ico",
    bundle_identifier="io.eggie.app",
    version=os.environ.get("EGGIE_VERSION", "0.0.0"),
    info_plist={
        # Both of these correct what BUNDLE infers, and both were wrong in a
        # build that otherwise looked clean:
        #
        # CFBundleExecutable is taken from the first executable in COLLECT's
        # own (sorted) table, which is `eggie` however the spec orders them --
        # so a double-click ran the CLI. LSBackgroundOnly is set from the
        # collection's console flag, and an app marked background-only has no
        # Dock icon and cannot bring its window forward.
        "CFBundleExecutable": "eggie-setup",
        "LSBackgroundOnly": False,
        # vz — the VM type eggie.yaml asks Lima for — is macOS 13+.
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        # Nothing here opens documents or takes a URL scheme; the window is the
        # whole interface.
        "CFBundleName": "Eggie",
        "CFBundleDisplayName": "Eggie",
        "CFBundleShortVersionString": os.environ.get("EGGIE_VERSION", "0.0.0"),
    },
)
