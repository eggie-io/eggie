# host/desktop/ — the native window

A pywebview window over the system webview (WebView2 on Windows, WKWebView on macOS); it replaced
the old tkinter wizard. `cli.setup` launches it. Tests: `tests/host/desktop/`.

## Module roles — keep them this way

- `view.py` — **pure** mappings from `host/core` values to what a screen needs. No provider, no
  client, nothing reaching the VM or network (it does walk the local filesystem in
  `inspect_folder()`). This is where real decisions go, because it is the one module worth unit-testing.
- `api.py` — `DesktopApi`, the **only** object JavaScript can reach. A thin, fixed list of methods
  taking scalars; push every decision into `view.py`.
- `shell.py` — `Shell` tracks which page is showing; `guarded()` wraps each public `DesktopApi`
  method so it refuses calls (`NotLocalPage`) unless the local UI is the page showing. A new
  `DesktopApi` method is covered automatically.
- `jobs.py` — runs **one** slow job at a time on a worker thread (`JobBusy` otherwise):
  `InstallState` is a JSON file and two installs writing it would race.
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
