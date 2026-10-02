"""Invariants of the bundled UI, not its rendering.

There is no JS test runner in this repo and one is not being added to assert
that a template renders. What is worth pinning is the boundary: a desktop app
whose fonts come from a CDN is broken on a train, and one that can reach any
remote origin has a bridge worth attacking.
"""
from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[3] / "host" / "desktop" / "ui"

# Matches a remote origin in markup, CSS or JS. Deliberately not anchored to
# src=/href=: a fetch() or an @import is the same problem.
REMOTE = re.compile(r"https?://(?!localhost|127\.0\.0\.1)", re.IGNORECASE)


def _assets() -> list[Path]:
    files = [p for p in UI.rglob("*") if p.suffix in {".html", ".css", ".js"}]
    # A relative Path() would pass vacuously from another directory; this has
    # bitten the repo three times, so assert we scanned something.
    assert files, f"no UI assets found under {UI}"
    return files


def test_no_asset_reaches_a_remote_origin():
    offenders = []
    for path in _assets():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if REMOTE.search(line):
                offenders.append(f"{path.relative_to(UI)}:{number}: {line.strip()}")
    assert not offenders, "UI assets must work offline:\n" + "\n".join(offenders)


def test_the_three_typefaces_are_bundled():
    names = {p.name for p in (UI / "fonts").glob("*.woff2")}
    for family in ("bricolage-grotesque", "hanken-grotesk", "ibm-plex-mono"):
        assert any(n.startswith(family) for n in names), f"missing {family}"


def test_the_font_licenses_ship_with_them():
    # All three are SIL OFL 1.1, which requires the license to travel with
    # the binary.
    assert (UI / "fonts" / "OFL.txt").is_file()


def test_the_page_forbids_remote_origins_at_runtime():
    head = (UI / "index.html").read_text()
    assert "Content-Security-Policy" in head
    assert "default-src 'self'" in head


def test_every_home_state_has_a_template():
    markup = (UI / "index.html").read_text()
    for screen in ("home:not_installed", "home:stopped", "home:running",
                   "home:wrong", "unreachable", "first-run",
                   "updates"):
        assert f'data-screen="{screen}"' in markup, f"no template for {screen}"


def test_every_home_state_can_offer_the_app_update():
    markup = (UI / "index.html").read_text()
    for screen in ("home:not_installed", "home:stopped", "home:running", "home:wrong"):
        start = markup.index(f'data-screen="{screen}"')
        end = markup.index("</template>", start)
        assert 'data-when="app_update"' in markup[start:end], screen


def test_no_template_renders_a_separator_with_nothing_after_it():
    """runtime_version is "" on every machine where the runtime never
    installed, so a hard-coded separator between two fields renders as
    "0.1.0 · "."""
    markup = (UI / "index.html").read_text()
    occurrences = [m.start() for m in re.finditer(r'data-field="runtime_version"', markup)]
    assert occurrences, 'no data-field="runtime_version" found'
    # Every runtime_version field must sit inside a section gated on it: the
    # nearest data-when before each occurrence must be the same field, or an
    # unconditional separator renders as "0.1.0 · " with nothing after it.
    for pos in occurrences:
        gates = re.findall(r'data-when="([^"]*)"', markup[:pos])
        assert gates, f'no data-when gate precedes data-field="runtime_version" at offset {pos}'
        assert gates[-1] == "runtime_version", (
            f'data-field="runtime_version" at offset {pos} is gated by '
            f'data-when="{gates[-1]}", not "runtime_version"')
    assert '</span> &middot; <span data-field="runtime_version">' not in markup
    assert '</span> · <span data-field="runtime_version">' not in markup


def test_the_version_tiles_bind_fields_desktop_api_home_actually_returns():
    """The markup and DesktopApi.home() agree on field names only by two
    people copying the same string correctly -- nothing pins them together.
    A diff that renamed Readiness.runtime_version (or home()'s key) but left
    the markup saying data-field="engine_version" would pass every other test
    here and render the version tile permanently blank."""
    from host.core.install import InstallState
    from host.core.status import Readiness
    from host.desktop.api import DesktopApi

    class FakeProvider:
        pass

    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          runtime_version="runtime-v0.1.0", api_version=1)
    state = InstallState(UI / "does-not-exist" / "install-state.json")
    home = DesktopApi(FakeProvider(), state, push=lambda event: None,
                      probe_fn=lambda provider: readiness).home()

    markup = (UI / "index.html").read_text()
    lines = [l for l in markup.splitlines() if 'data-field="app_version"' in l]
    assert lines, "no version tile found"
    gated = [l for l in lines if 'data-when="runtime_version"' in l]
    # home:not_installed's tile shows app_version alone -- there is no runtime
    # yet to name -- so only the tiles gated on the runtime being present are
    # required to bind a data-field for it.
    assert gated, "no version tile is gated on runtime_version"
    for line in lines:
        fields = re.findall(r'data-field="([a-zA-Z_]+)"', line)
        for field in fields:
            assert field in home, (
                f"{field!r} is not a key DesktopApi.home() returns; a version "
                f"tile bound to it renders permanently blank: {line.strip()}")
    for line in gated:
        fields = re.findall(r'data-field="([a-zA-Z_]+)"', line)
        assert "runtime_version" in fields, \
            f"version tile has no data-field for the runtime version: {line.strip()}"


def test_nothing_installed_offers_no_uninstall():
    """There is no VM to remove; the button could only error or no-op."""
    markup = (UI / "index.html").read_text()
    start = markup.index('data-screen="home:not_installed"')
    end = markup.index("</template>", start)
    assert 'data-action="uninstall"' not in markup[start:end]


def test_nothing_installed_disables_the_import_tile():
    markup = (UI / "index.html").read_text()
    start = markup.index('data-screen="home:not_installed"')
    end = markup.index("</template>", start)
    screen = markup[start:end]
    tile = screen.index('data-action="import"')
    assert "disabled" in screen[tile:tile + 80], "import tile must be disabled"


def test_no_user_facing_copy_names_one_platform():
    """The app ships on Windows and macOS and may not branch on platform, so a
    sentence naming either one is false on the other."""
    import re
    markup = (UI / "index.html").read_text()
    assert not re.search(r"\b(Mac|macOS|Windows|PC)\b", markup), \
        "platform-specific copy in a cross-platform UI"


def test_every_install_state_has_a_template():
    markup = (UI / "index.html").read_text()
    for screen in ("install:running", "install:reboot", "install:failed"):
        assert f'data-screen="{screen}"' in markup, f"no template for {screen}"


def test_the_install_panel_has_no_hard_coded_step_count():
    # "Step 4 of 7" is a macOS fact. Windows runs nine.
    markup = (UI / "index.html").read_text()
    assert "of 7" not in markup


def test_the_resume_notice_is_the_exact_sentence_from_install_py():
    """The old tkinter UI's RESUME_NOTICE is copied character for character
    into the HTML, gated so it only shows on a resumed launch."""
    install_py = (Path(__file__).resolve().parents[3] / "host" / "core"
                  / "install.py").read_text()
    match = re.search(r'RESUME_NOTICE = "(.+)"', install_py)
    assert match, "RESUME_NOTICE not found in host/core/install.py"
    notice = match.group(1)

    markup = (UI / "index.html").read_text()
    start = markup.index('data-screen="install:running"')
    end = markup.index("</template>", start)
    screen = markup[start:end]
    assert notice in screen
    notice_line = next(line for line in screen.splitlines() if notice in line)
    assert 'data-when="resumed"' in notice_line


def test_the_step_counter_is_clamped_to_the_total():
    """A 7-step run must not end by announcing "Step 8 of 7"."""
    js = (UI / "app.js").read_text()
    assert "Math.min(" in js, "step counter must be clamped to the total"


def test_the_progress_bar_tracks_completed_steps_not_the_download():
    """The old code bound the bar to event.fraction, which is non-null only on
    the download step, so the bar sat full through the several minutes after
    it. Assert the specific expression, not tokens that predate the fix."""
    js = (UI / "app.js").read_text()
    assert "window.omelet.done / window.omelet.total" in js, \
        "the overall bar must be driven by completed steps"


def test_only_a_dead_end_removes_the_retry_button():
    """crashed and failed are both retryable; dead_end is not (a blocking
    check no code can fix would loop the user forever). Stripping retry for
    all three, or for none, are both regressions."""
    js = (UI / "app.js").read_text()
    retry = js.index("[data-retry]")
    guard = js.rindex("dead_end", 0, retry)
    # The retry removal must sit inside a branch testing dead_end specifically,
    # not inside the shared failed/crashed branch.
    assert "crashed" not in js[guard:retry], \
        "retry removal must be guarded by dead_end alone"


def test_the_utility_screens_have_templates():
    markup = (UI / "index.html").read_text()
    for screen in ("import", "import:progress", "doctor",
                   "uninstall-confirm", "unresponsive", "recover-confirm"):
        assert f'data-screen="{screen}"' in markup, f"no template for {screen}"


def test_purge_is_not_preselected():
    # Uninstall removes the VM; purge additionally deletes everything on disk.
    markup = (UI / "index.html").read_text()
    purge = markup.index('name="purge"')
    assert "checked" not in markup[purge:purge + 120]


def test_replace_is_not_the_preselected_import_mode():
    # Replace deletes files with no undo. The board preselects Merge; a
    # stray Enter on this screen must not wipe a project.
    markup = (UI / "index.html").read_text()
    merge = markup.index('value="merge"')
    replace = markup.index('value="replace"')
    assert "checked" in markup[merge:merge + 120]
    assert "checked" not in markup[replace:replace + 120]


def test_a_crashed_job_tells_the_user_something_went_wrong():
    """import/vm/repair/uninstall crashes used to bounce the user Home with
    the reason discarded."""
    js = (UI / "app.js").read_text()
    assert js.count("showNotice(") >= 4, "every job kind must surface a crash"
    markup = (UI / "index.html").read_text()
    assert 'id="notice"' in markup


def test_the_notice_survives_a_screen_change():
    """It must sit outside the templates: show() replaces #screen wholesale."""
    markup = (UI / "index.html").read_text()
    notice = markup.index('id="notice"')
    # Everything from the first <template> onwards is swapped out by show().
    assert notice < markup.index("<template"), "notice must precede the templates"


def test_hidden_beats_any_display_rule():
    """#notice is display:flex, which outranks the UA's [hidden] rule: the
    notice showed on every launch with no detail, and Dismiss did nothing."""
    css = (UI / "app.css").read_text()
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", css)


def test_every_bridge_call_in_the_ui_names_a_real_method():
    """A misspelled api().method() only rejects at runtime, inside a window
    no test opens."""
    from host.desktop.api import DesktopApi
    from host.desktop.shell import public_methods

    script = (UI / "app.js").read_text()
    called = set(re.findall(r"api\(\)\.([a-zA-Z_]+)\(", script))
    assert "enter_console" in called
    assert called <= set(public_methods(DesktopApi)), called - set(public_methods(DesktopApi))


def test_the_runtime_update_screens_exist_and_auto_start_once():
    markup = (UI / "index.html").read_text()
    for screen in ("runtime-update:running", "runtime-update:failed"):
        assert f'data-screen="{screen}"' in markup
    script = (UI / "app.js").read_text()
    # Auto-starting on every refresh would loop forever on an update that
    # "succeeds" without fixing the API.
    assert "runtimeUpdateTried" in script


def test_the_page_lets_pywebview_build_its_bridge():
    """The CSP must allow dynamic code evaluation for scripts, or the window
    opens empty.

    pywebview builds every bridge method with `new Function(params, body)` in
    its own api.js, which it injects into the page after load. `new Function`
    is governed by script-src, and with only `default-src 'self'` WebKit
    refuses it: `_createApi` throws on the first method, `window.pywebview.api`
    stays `{}`, and the `pywebviewready` event at the end of pywebview's
    finish.js never fires -- so app.js's listener never calls refresh() and
    `#screen` is never filled. The window shows the background colour and
    nothing else, with no error anywhere: the injection is fire-and-forget on
    the Python side.

    WebView2 runs host-injected script outside the page's CSP, so Windows
    never showed this; WKWebView runs it in the page's world, so macOS did.
    Remote script stays blocked either way -- 'self' is still the only origin.
    """
    head = (UI / "index.html").read_text()
    match = re.search(r"script-src ([^;\"]*)", head)
    assert match, "CSP names no script-src, so scripts fall back to " \
                  "default-src and pywebview's bridge cannot be built"
    sources = match.group(1).split()
    assert "'unsafe-eval'" in sources, \
        f"script-src must allow 'unsafe-eval' for pywebview's api.js: {sources}"
    assert "'self'" in sources, f"script-src must still pin 'self': {sources}"


def test_the_tray_screens_have_templates():
    markup = (UI / "index.html").read_text()
    for screen in ("settings", "quit-confirm", "quitting"):
        assert f'data-screen="{screen}"' in markup, f"no template for {screen}"


def test_every_home_state_can_open_settings():
    markup = (UI / "index.html").read_text()
    for screen in ("home:not_installed", "home:stopped", "home:running", "home:wrong"):
        start = markup.index(f'data-screen="{screen}"')
        end = markup.index("</template>", start)
        assert 'data-action="settings"' in markup[start:end], screen


def test_the_settings_copy_is_exact():
    markup = (UI / "index.html").read_text()
    assert "Open Omelet when I sign in" in markup
    assert "Stopping Omelet…" in markup


def test_the_page_answers_the_routes_the_tray_sends():
    # controller.open_route() sends exactly these names, by call or by #fragment.
    script = (UI / "app.js").read_text()
    assert "window.omelet.route =" in script
    assert "addEventListener('pywebviewready', start)" in script
    start = script[script.index("function start()"):]
    assert "window.location.hash" in start.split("\n}\n")[0]
    routes = script[script.index("const ROUTES"):].split("};")[0]
    for route in ("settings", "quit"):
        assert f"'{route}'" in routes, f"ROUTES does not handle {route}"


def test_cancelling_a_tray_route_restores_the_interrupted_screen_before_refreshing():
    script = (UI / "app.js").read_text()
    for action in ("quit-cancel", "settings-back"):
        body = script[script.index(f"ACTIONS['{action}']"):].split("\n")[0]
        assert "restoreScreen()" in body and "refresh()" in body, action
        assert body.index("restoreScreen()") < body.index("refresh()"), action
    assert 'data-action="settings-back"' in (UI / "index.html").read_text()


def test_the_page_handles_the_end_of_a_background_start():
    from host.desktop.controller import BACKGROUND_DONE
    script = (UI / "app.js").read_text()
    handler = f"window.omelet.handlers.{BACKGROUND_DONE['kind']} ="
    assert handler in script
    body = script[script.index(handler):].split("\n};")[0]
    # Only Home is stale; refreshing anywhere else throws the user off their screen.
    assert "startsWith('home:')" in body and "refresh()" in body
