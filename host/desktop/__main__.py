"""Build the provider, open the window, hand the loop to pywebview.

`webview.start()` owns the main thread for the life of the app, so this
module does nothing after calling it. Everything that happens later happens
on a worker thread from jobs.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

WINDOW_TITLE = "Omelet"
# The board's frames are 880x620. The OS draws the title bar, so that is the
# content size; min_size keeps the nine-row install panel from clipping.
WINDOW_SIZE = (880, 620)
MIN_SIZE = (800, 560)

WEBVIEW_MISSING = (
    "Omelet could not open its window because this computer is missing the "
    "Microsoft Edge WebView2 runtime.\n"
    "Install it from https://developer.microsoft.com/microsoft-edge/webview2/ "
    "and open Omelet again.\n"
    "In the meantime you can still set up Omelet by running: "
    "omelet setup --headless"
)


def ui_dir() -> Path:
    """Where the HTML lives, in a checkout and inside the frozen binary.

    `sys._MEIPASS` is PyInstaller's unpack directory. Reading it is an
    attribute check, not a platform check, so tests/test_no_platform_leak.py
    stays satisfied.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "host" / "desktop" / "ui"
    return Path(__file__).resolve().parent / "ui"


def _default_create(**kwargs):
    import webview
    return webview.create_window(**kwargs)


def _default_start(**kwargs):
    import webbrowser

    import webview

    from .shell import web_links_only

    # pywebview looks webbrowser.open up at call time, so this covers its
    # new-window handling on every backend.
    webbrowser.open = web_links_only(webbrowser.open)
    webview.start(**kwargs)


def run(provider, state, *, create=_default_create, start=_default_start,
        resumed: bool = False, steps_factory=None, app_update_fn=None) -> int:
    from .api import DesktopApi
    from .shell import Shell, guarded

    shell = Shell()
    api = DesktopApi(provider, state, push=shell.push, steps_factory=steps_factory,
                     local_url=shell.local_url, app_update_fn=app_update_fn)
    # Surfaced by a later task: the install screen reads this to show
    # host.core.install.RESUME_NOTICE when RunOnce reopened the window.
    api.resumed = resumed
    api.start_app_update_check()

    try:
        window = create(title=WINDOW_TITLE, url=str(ui_dir() / "index.html"),
                        js_api=None, width=WINDOW_SIZE[0],
                        height=WINDOW_SIZE[1], min_size=MIN_SIZE)
    except Exception as e:
        # Deliberately broad: a missing runtime surfaces differently on each
        # backend, and pywebview cannot be imported here to catch its own
        # type. The repr goes out too because this same handler catches
        # ordinary bugs -- without it, a typo'd kwarg reads to the user as
        # "install WebView2", which would not help and would not be true.
        print(WEBVIEW_MISSING, file=sys.stderr)
        print(f"(technical detail: {e!r})", file=sys.stderr)
        return 3

    shell.window = window
    window.expose(*guarded(api, shell))
    start(debug=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    from host.core import constants
    from host.core.install import VERIFY_TEMPLATE, InstallState, default_steps
    from host.providers import default_install_dir, get_provider

    parser = argparse.ArgumentParser(prog="omelet-desktop")
    # Written by provider.register_resume() into Windows RunOnce. The flag
    # must keep working or a restarted machine never finishes setup.
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)

    root = default_install_dir().parent
    state = InstallState(root / "install-state.json")
    provider = get_provider()

    # Mirrors host.cli.setup's build_steps exactly: launching this module
    # directly and launching it via `omelet setup` must install identically.
    def build_steps():
        return default_steps(
            provider,
            cache_dir=root / "cache",
            template_dir=VERIFY_TEMPLATE,
            domain=constants.DEFAULT_DOMAIN,
            exe_path=sys.executable,
        )

    return run(provider, state, steps_factory=build_steps, resumed=args.resume)


if __name__ == "__main__":
    raise SystemExit(main())
