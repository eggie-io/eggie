# Projects console inside the desktop app — design

Date: 2026-09-23
Issue: #14

> **Update (same day):** the native "Eggie" menu (§3) was removed in favour
> of the console's own top bar (§7): **‹ Home** replaces
> *Machine*, **↗** replaces *Open in browser*, and `DesktopApi.open_eggie`
> and `Shell.load_local` went with it. Sections below that mention the menu
> describe the first iteration. The bar needs a runtime release: the
> published `eggie-web:0.2.0` (pinned by `runtime-v0.0.5`) predates it, and a
> console without the bar has no way back to Home inside the window.

## 1. Why

A user today meets two interfaces: the desktop app (install, start/stop, ports,
Doctor, uninstall, folder import) and the browser console at
`http://localhost:39080` (projects, files, uploads), which the desktop's
**Open Eggie** button opens in the system browser. For a non-technical user
those read as two different products. The console moves into the desktop
window; the browser stays available as a secondary route.

What the user decided:

- Host-only controls live behind a **native "Eggie" menu**, not a strip drawn
  around the console and not a "Machine" section inside the console.
- **Import folder stays** in the desktop app for now, even though the console
  has uploads.
- **"Open in browser" stays** as a menu item.

## 2. Shape: one window, two kinds of page

The window keeps loading the local desktop UI (`host/desktop/ui/index.html`)
first. When the machine is running it navigates the same window to the console
with `window.load_url`. There is no frame and no second window. The console's
own top bar (§7) is what makes the two feel like one app; the native menu
stays as the fallback for a runtime older than that bar.

Rejected:

- **Console in an iframe inside a desktop strip.** The top document is a
  `http://127.0.0.1:<random>` page (pywebview serves an absolute local path
  through its own bottle server) and the console is `http://localhost:39080`,
  so the console's
  session cookie is a cross-site cookie inside the frame and is not sent. It
  also needs a `postMessage` protocol between the two.
- **Two windows, one shown at a time** (the console window created without
  `js_api`). Strongest isolation, but keeping two native windows' size and
  position in step is visible jank on both platforms. The guard in §4 covers
  this threat except for the race it names.
- **A "Machine" section inside the console.** The runtime would start knowing
  about the host, and a new host control would need a runtime release.

## 3. Navigation

### Entering the console

A new `DesktopApi.enter_console()`:

1. asks the API for a handoff code (`ApiClient.handoff_code()`),
2. calls `window.load_url(f"http://localhost:{EDGE_PORT}/#handoff={code}")`,
3. returns `{"ok": True}`.

If the handoff throws (stopped VM, unreadable token, older API) it returns
`{"ok": False, "message": ...}` and the window stays where it is. It never
loads the console without a code: inside the app, the console's signed-out
screen tells the user to open Eggie from the desktop app — which is where
they already are — so a bare load is a dead end. `open_eggie` keeps its
bare-URL fallback, because in a real browser that screen is the right one.

### When it happens

- **At launch**, once. `home()` gains a one-shot `enter_console` flag, true
  only on the first call of the process and only when the route is
  `home · running` and neither `first_run` nor `resumed` applies. The JS boot
  calls `enter_console()` when it is set. One-shot for the same reason
  `resumed` is: without it, choosing *Machine* from the menu reloads the local
  UI, re-probes, sees "running" and bounces straight back into the console.
- **From the "The kitchen is open" screen**: its **Open Eggie** button calls
  `enter_console()` instead of `open_eggie()`. On `ok: false` the screen shows
  the message in the existing notice.
- **From the menu**, below.

### The Eggie menu

Passed to `webview.start(menu=...)`. Callbacks are Python functions; nothing
crosses the JS bridge.

| Item | Does |
|---|---|
| Projects | `enter_console()`; on failure a notice on the local UI, or — from the console — back to the local UI, whose Home state explains it |
| Machine | `window.load_url(<local UI>)` — the local UI boots, re-probes and shows Home in its real state; nothing when the local UI already shows |
| Open in browser | `open_eggie()` (today's behaviour: handoff, system browser) |

*Projects* does nothing while a desktop job (install, import, VM start/stop,
repair, uninstall) is running: the local screen is showing that job's
progress, which is the explanation, and leaving it would lose the job's
events (§4).

The window title is "Eggie" while the console is showing.

## 4. Security: the bridge is not the console's

pywebview injects `window.pywebview.api` into **every** page loaded in the
window, the console included. The console is served from inside the VM, where
coding agents run with root; anything there can replace the `eggie-web`
container. Unguarded, a page from the VM could call `reboot_now`,
`start_uninstall`, or `start_import` with an arbitrary host path.

So:

- **Every public `DesktopApi` method is guarded.** The shell records the
  local UI's URL the first time it sees an `http(s)` URL — before the first
  navigation away, the only page the window has shown is the local UI, and
  navigation is refused until that URL is known. A guarded method reads
  `window.get_current_url()` and raises unless it equals that URL (ignoring
  the fragment). The console can see the method names and run none of them.
  The guard is applied to the class as a whole, not method by method, so a
  method added later cannot forget it. The guarded functions are registered
  with `window.expose()` and `js_api` stays `None`: pywebview resolves a
  dotted call name from `js_api` with plain `getattr`, with no underscore
  filter, so any object there lets a page walk `home.__func__.__globals__`
  into the host process without passing the guard.
- **What the guard does not cover.** It checks the page showing when the call
  is handled, not the page that sent it; pywebview does not expose the
  sender. The local UI is plain `http://127.0.0.1:<port>`, so a console page
  that finds the port can navigate the window there itself. Landing on the
  genuine local UI gains it nothing — its own script is gone — but a call
  posted just before that navigation commits and handled just after would
  pass. Closing that needs the rejected two-window shape.
- **Job events are pushed only to the local UI.** `push` checks the current
  URL the same way before `evaluate_js`, so a console page that defines its
  own `window.eggie.on` never receives progress events.
- `private_mode` stays at its default (on): the console's session cookie does
  not outlive the app, and every launch starts from a fresh handoff.

## 5. Edge cases

- **VM dies while the console is open.** The console shows its own "Eggie
  isn't answering" screen. *Machine* re-probes and shows the real state with
  its buttons. No background polling.
- **Session expired or handoff spent.** The console shows "We've lost track of
  you"; *Projects* issues a fresh handoff.
- **Links to a user's app** (`*.localhost:39080`). The console opens them with
  `target="_blank"` and `window.open(..., "_blank")`; pywebview's
  `OPEN_EXTERNAL_LINKS_IN_BROWSER` (default on) sends them to the system
  browser. On WKWebView pywebview forwards only link activations, so the
  console opens every external address with an anchor click
  (`desktop/desktop.ts`'s `openExternal`), never `window.open`.
- **Install finishing.** The install screen returns to Home as today; there is
  no automatic jump into the console after an install (the launch flag is
  already spent). The user presses **Open Eggie**.

## 6. Testing

Tests where a wrong result is plausible:

- **The guard.** Every public `DesktopApi` method raises when the current URL
  is the console, or any URL other than the recorded local one, and works on
  the local one. The test enumerates the class's public methods rather than a
  hand-kept list.
- **`push`** drops events when the current URL is not the local UI.
- **`enter_console()`** loads `…/#handoff=<code>` on success; on a failing
  handoff it loads nothing and returns `ok: false`.
- **`home()`'s `enter_console` flag** is true once, only for `running`, and
  never alongside `first_run` or `resumed`.
- **Menu wiring**, through `run()`'s existing `create`/`start` seams: `start`
  receives a menu with Projects, Machine and Open in browser.

Left untested on purpose: pywebview's `load_url`, menu rendering and
external-link handling — that is the library. Covered by the manual
acceptance run instead: enter the console at launch, *Machine* and back,
*Open in browser*, a project link and a `window.open` button opening in the
system browser, and (from devtools in a debug build) a call to
`window.pywebview.api.doctor()` from the console being refused.

## 7. The console's top bar inside the desktop

The native menu is the fallback. The primary way back is a bar the console
draws itself, the same design as the rest of the page:

```
[‹ Home] | (egg) Projects                    ● KITCHEN OPEN   [↗]
```

- **How the console knows it is inside the desktop.** `enter_console()` adds
  the local UI's address to the handoff link:
  `#handoff=<code>&home=<percent-encoded http://127.0.0.1:<port>/index.html>`.
  The console reads it before `takeHandoff()` clears the fragment
  (`desktop/desktop.ts`), keeps it in `sessionStorage` so a reload keeps the
  bar, and accepts only `http://127.0.0.1:<port>` with no userinfo — the link
  can be crafted, and Home must never lead off the machine. In a plain browser
  there is no `home`, and the bar is what it was.
- **‹ Home** is a plain link to that address. The console gains no power by
  it: leaving the page is what the *Machine* menu item already does, and the
  local UI's one-shot launch flag is spent, so Home stays on Home. It shows on
  the status screens too (signed out, not answering), which inside the app
  were dead ends.
- **Kitchen open** shows whenever the console is signed in — the API answered.
- **Open in browser (↗)**, inside the desktop only, signs the system browser
  in: `POST /api/sessions/handoff` mints a code with the page's own cookie
  session (the middleware still checks the cookie and the `Origin`), and the
  page opens `http://localhost:39080/#handoff=<code>`. A code minted by a
  signed-in page is no more power than the cookie it already holds. The code
  is fetched on hover/focus so the click itself opens the link: WKWebView
  blocks a new window opened after an `await`. With no code (old API, lost
  session) it opens the bare page, whose sign-in screen explains itself.
- No sync marker: it stays hidden, as the web UI's scope already decided.

This part is a runtime change. It needs new image tags: `0.2.0` is already
published without it (pinned by `runtime-v0.0.5`).
