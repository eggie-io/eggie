# host/desktop/ — the native window

A pywebview window over the system webview (WebView2 on Windows, WKWebView on macOS); it replaced
the old tkinter wizard. `cli.setup` launches it. Tests: `tests/host/desktop/`.

## Module roles — keep them this way

- `view.py` — **pure** mappings from `host/core` values to what a screen needs. No provider, no
  client, nothing reaching the VM, network or filesystem. This is where real decisions go, because
  it is the one module worth unit-testing.
  `screen_for()` decides what Home draws and the one action the page takes first (`home()` returns
  it as `screen` / `action` / `message`); `refresh()` in `app.js` only dispatches on those. Add a
  screen name to `SCREENS` and its template lands in `test_ui_assets.py`'s check.
- `api.py` — `DesktopApi`, the **only** object JavaScript can reach. A thin, fixed list of methods
  taking scalars; push every decision into `view.py`.
- `shell.py` — `Shell` tracks which page is showing; `guarded()` wraps each public `DesktopApi`
  method so it refuses calls (`NotLocalPage`) unless the local UI is the page showing. A new
  `DesktopApi` method is covered automatically.
- `jobs.py` — runs **one** slow job at a time on a worker thread (`JobBusy` otherwise):
  `InstallState` is a JSON file and two installs writing it would race.
- Two platform objects reach here, both from `host/providers`: the `VmProvider` (the machine) and
  the `DesktopPlatform` (tray, single instance, login). `run()`, `Controller` and `DesktopApi`
  take both; never reach a desktop method through the provider.
- `controller.py` — the window and tray as one app: close hides, `exit()` really closes, tray
  routes reach the page via `window.eggie.route()` or a `#route` reload when the console shows.
  If the tray cannot start, the app runs as a plain window for that run (close exits) and the
  failure goes to the log.
- `log.py` — the log file (`eggie.log` next to `settings.json`, rotated), set up by `run()` and
  named by the Doctor modal. **Never `print(..., file=sys.stderr)` here**: the Windows build is
  windowed, so `sys.stderr` is `None` and the line is lost. Use `logging.getLogger(__name__)`;
  stderr is mirrored while one exists. Job crashes land here with their traceback.
- `lifecycle.py` / `settings.py` — pure launch-mode decision and the `settings.json` flags that
  must outlive the VM (`install-state.json` is deleted on reset).
- `ui/` — HTML/CSS/JS and bundled fonts. Must work fully offline. This is **not** the browser
  console in `runtime/web/`; don't share assets between them.

When the VM is running, the same window shows the projects console (`localhost:<edge>`, entered with
a handoff code). The console's own top bar links back to the local screens and opens itself in the
system browser — there is no native menu.

## Things that will bite you

- **pywebview injects `window.pywebview.api` into every page the window loads**, including the
  console served from the VM. Hence the guard. Keep `create_window(js_api=None)` and register
  functions with `window.expose()`: pywebview resolves a dotted call name from `js_api` with plain
  `getattr`, so any object there lets a page walk `home.__func__.__globals__` past the guard. The
  guard checks the page showing, not the sender, and the local UI is plain
  `http://127.0.0.1:<port>`; the spec's "What the guard does not cover" names the residual race.
- Any page can open a new window, which pywebview hands to `webbrowser.open` (`os.startfile` on
  Windows), so `_default_start` wraps it with `web_links_only` — http(s) only.
- `ui/index.html`'s CSP must keep `script-src 'self' 'unsafe-eval'`. pywebview builds every bridge
  method with `new Function(...)`; under a bare `default-src 'self'` WebKit refuses it,
  `window.pywebview.api` stays `{}`, `pywebviewready` never fires, and the window shows only its
  background colour — with nothing on stderr. Invisible on Windows (WebView2 runs injected script
  outside the CSP), fatal on macOS. Pinned by `tests/host/desktop/test_ui_assets.py`.
- **`events.closing` decides closes** — on Windows the title-bar button and `window.destroy()`
  both fire it; on Cocoa only the red button does (`destroy()` is `NSWindow.close`, which skips
  `windowShouldClose_`, and the mac tray re-points Cmd+Q at Quit Eggie and overrides
  `applicationShouldTerminate_`, so neither reaches it). `Controller` lets a close through only after
  `exit()` or `allow_exit()`. A new close path must go through one of them or it becomes a hide.
- **Windows sign-out, restart and installers close the window themselves.** pywebview cancels any
  close our handler refuses, whatever the reason, so `desktop.let_session_end_close` wraps the
  winforms form's `on_closing` before `create()` and calls `Controller.allow_exit()` for
  `WindowsShutDown` / `TaskManagerClosing`.
- **macOS Dock → Quit, logout and restart do not stop the VM.** `tray_mac` overrides
  `applicationShouldTerminate_` to `NSTerminateNow`; otherwise the close-to-hide handler cancels
  termination and blocks logout.
- **macOS tray callbacks run on a daemon thread.** pywebview's Cocoa window methods dispatch to
  the main thread and wait, so calling them from the main thread deadlocks.
- **Quit is not a job.** `JobRegistry` runs one job; Quit anyway must work while an install holds
  it. Quit exits even when stopping the VM fails.
- **Single instance on Windows is a per-user named pipe without an authkey.** The key
  authenticated nothing and blocked `accept()`.
