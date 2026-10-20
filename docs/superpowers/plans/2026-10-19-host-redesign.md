# Host Desktop Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle every host desktop screen to the Eggie design system and recolour the native Windows title bar, with no change in behaviour.

**Architecture:** `host/desktop/ui/` (static HTML/CSS/JS in a pywebview window) gets the design system's tokens, fonts and component styles; existing class names, `data-action`s, `data-field`s and screen names stay, so `app.js` and the Python bridge are untouched except where the install rows render. The title-bar recolour is a new `DesktopPlatform.style_title_bar(window)` method — real on Windows (DWM attributes), no-op on macOS — called by `host/desktop/__main__.run()` before the window first shows.

**Tech Stack:** Python 3.12, pywebview 5–6 (WinForms/WebView2, Cocoa/WKWebView), plain HTML/CSS/JS, pytest. Headless Chrome via Playwright for visual checks.

**Spec:** `docs/superpowers/specs/2026-10-19-host-redesign-design.md`

## Global Constraints

- `host/` only. Nothing under `runtime/` changes.
- No new behaviour: same screens, buttons, `data-action` names, `data-field` names, `DesktopApi` methods. Copy stays as today on every screen.
- UI works offline; no remote origin in any `.html/.css/.js` under `host/desktop/ui/`. CSP in `index.html` unchanged.
- No `sys.platform` / `platform.system()` / `os.name` outside `host/providers/` (`tests/test_no_platform_leak.py`).
- Window size unchanged: 880×620, min 800×560; window stays resizable and maximizable. Nothing clips at 800×560.
- Theme follows the OS via `@media (prefers-color-scheme: dark)`; the design system's dark values go there.
- Design references (this session's scratchpad, `S=/tmp/claude-1000/-home-ihor-projects-local-environment-for-non-tech-poc/4d2622ac-c66e-44e2-8f46-a26df32ccc57/scratchpad`): `$S/design/ds/ds-reference.css` (tokens + component CSS), `$S/design/v3.html` lines 39–227 (frames 7a–7d, the host), `$S/design/ds/fonts/*.woff2`, `$S/design/ds/_ds_bundle.js` (`iconPaths`, ~line 150).
- Tests: `TMPDIR=/home/ihor/tmp python3 -m pytest -q` (system python3 is 3.12; there is no .venv).
- Visual check harness: `node $S/preview/preview.mjs <outdir> [scenario,…|all] [light|dark|both] [880x620,800x560]` renders every screen with the bridge stubbed and prints `rendered without page errors` or a list of page errors / horizontal overflow. Baseline of today's UI: `$S/preview/before/`. Read the PNGs to check them.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `feature/93-host-redesign` (already checked out).
- Code comments: only for non-obvious edge cases; no ticket/doc references.

## Review Focus

1. **Minimum window size (800×560)** on the longest screens — install with 9 rows, home:wrong with a long problem, uninstall confirm — primary buttons must stay visible without scrolling the page. Owned by Tasks 2–4; each runs the harness at 800x560 and checks the primary action is on-screen.
2. **Dark mode** — every token has a dark value; no hard-coded light colour leaks (e.g. `#fff` text on gold). Owned by Task 1 (tokens) and checked in every UI task with `both` themes.
3. **Long untrusted text** (problem/message logs from a subprocess, long version strings) must wrap or scroll inside its box and never widen the page. The harness flags horizontal overflow; Tasks 2 and 4 use the 30-line log scenarios.
4. **A title-bar call that fails** (Windows 10, no `native` handle, pythonnet error) must never stop the app from opening. Test in Task 5.
5. **Swapping install screens mid-run** (running → reboot / failed) keeps the rows' states and still shows "Step N of M" + overall bar. Owned by Task 3, checked with the `install-reboot` / `install-failed` scenarios.

---

### Task 1: Design-system foundations (fonts, tokens, shared component styles)

Every screen picks up the new look through the shared classes; layout changes come in later tasks.

**Files:**
- Modify: `host/desktop/ui/app.css` (token block, font faces, base, focus, `.btn-*`, `.badge*`, `.display`/`.title`/`.lede`/`.hint`/`.overline`, `pre.log`/`.log`, `.btn-copy`, `.warn`, `.choice`, `.setting`, `#notice`, `#busy`, icon masks)
- Add: `host/desktop/ui/fonts/archivo-latin-wdth-normal.woff2`, `host/desktop/ui/fonts/jetbrains-mono-latin-wght-normal.woff2` (copy from `$S/design/ds/fonts/`)
- Delete: `host/desktop/ui/fonts/{bricolage-grotesque-800,hanken-grotesk-400,hanken-grotesk-600,hanken-grotesk-700,ibm-plex-mono-400}.woff2`
- Modify: `host/desktop/ui/fonts/OFL.txt`
- Test: `tests/host/desktop/test_ui_assets.py`

**Interfaces:**
- Produces (used by Tasks 2–5): CSS custom properties with the design-system names and values from `ds-reference.css` — `--page --raised --paper --hover --line --line-strong --line-input --ink --ink-2 --ink-3 --gold --orange --coral --periwinkle --orchid --on-gold --gold-ink --gold-soft --accent --accent-hover --on-accent --accent-soft --accent-ink --focus --success(-soft) --warning(-soft) --danger(-soft) --danger-hover --on-danger --info(-soft) --code-bg --code-ink --code-ink-2`, `--font-sans --font-mono`, `--space-*`, `--radius-*`, `--shadow-sm/md/lg/glow`, `--control-sm/md/lg`. Each colour is declared **once** on `:root` and once inside `@media (prefers-color-scheme: dark) { :root { … } }`, in the form `--raised: #fefbf7;` (Task 5's test parses `--raised`, `--ink-2`, `--line` from these two blocks).
- Produces: icon mechanism — any element with `--icon: url("data:image/svg+xml,…")` and class `.ic` (or the existing `.tiles button::before`) draws that glyph as a `currentColor` mask. Define `--icon` values as reusable custom properties on `:root`: `--i-play --i-stop --i-external --i-refresh --i-activity --i-download --i-settings --i-trash --i-copy --i-check-circle --i-x-circle --i-alert-triangle --i-info`, built from `iconPaths` (viewBox `0 0 24 24`, `stroke="black" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" fill="none"`). Existing `.tiles [data-action="…"] { --icon: …; }` rules keep that exact textual form (pinned by `test_every_home_tile_has_an_icon`) and point at the new glyphs: doctor→activity, check-updates→download, settings→settings, uninstall→trash.
- Produces: restyled shared classes keep their names: `.btn-primary` (gold pill, `--on-gold` text, orange + `--shadow-glow` on hover, 40px `lg` size), `.btn-secondary` (paper, `--line-input` border, 40px), `.btn-danger`, `.btn-update`, `.btn-copy` (ghost, 28px, copy icon), `.badge` + `.badge-running`(success) `.badge-cold`(outline) `.badge-empty`(outline) `.badge-bad`(danger) `.badge-warm`(warning), `.display` (Archivo `wdth` 112, 650, 44px/50px, -0.02em), `.title` (wide 650, 28px/34px), `.lede` (16/24 `--ink-2`), `.hint` (13/18 `--ink-2`), `.overline` (11/16, 600, .06em, uppercase, `--ink-2`).

- [ ] **Step 1: Update the typeface test (fails first)**

In `tests/host/desktop/test_ui_assets.py` rename `test_the_three_typefaces_are_bundled` → `test_the_design_system_typefaces_are_bundled`, families `("archivo", "jetbrains-mono")`, and add `assert not any(n.startswith(("bricolage", "hanken", "ibm-plex")) for n in names)` so retired fonts can't ship as dead weight. Update the OFL test's comment ("both are SIL OFL 1.1").

- [ ] **Step 2: Run it — expect FAIL** (`missing archivo`)

`TMPDIR=/home/ihor/tmp python3 -m pytest tests/host/desktop/test_ui_assets.py -q`

- [ ] **Step 3: Swap the font files and licence**

Copy the two woff2 files in, `git rm` the five old ones. `OFL.txt`: two headed sections like today's (`=== Archivo ===` "Copyright 2017 The Archivo Project Authors (https://github.com/Omnibus-Type/Archivo)"; `=== JetBrains Mono ===` "Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)") followed by the existing OFL 1.1 text once.

- [ ] **Step 4: Rewrite the token block, font faces and shared component styles in `app.css`**

`@font-face` for Archivo with `font-weight:100 900; font-stretch:62% 125%` and JetBrains Mono `font-weight:100 800`, `src:url('fonts/…')`. Body: `--page` background, `--font-sans`, 14/20. Focus: `2px solid var(--focus)`, offset 2px. Remove old tokens (`--desk --cream --surface* --yolk* --basil* --paprika* --cold* --r-*`) and every use of them across the file — later tasks restyle layouts, but nothing may reference a deleted token after this task (grep must find none). Keep `[hidden]{display:none!important}`, the reduced-motion block, the `user-select:text` opt-in for logs, and the `button[aria-busy]` spinner (accent-coloured). `#notice`: danger-soft bar with a top `--danger` hairline, `--shadow-md`. `#busy`: 3px strip, `--accent`. Log `<pre>` inside `details.log` and `pre.log`: dark code block (`--code-bg`, `--code-ink`, mono 12/18, 12px radius, max-height 240px, scroll). Replace the "board" wording in comments.

- [ ] **Step 5: Run the UI tests — expect PASS**

`TMPDIR=/home/ihor/tmp python3 -m pytest tests/host/desktop -q`; then `grep -nE -- '--(desk|cream|surface|yolk|basil|paprika|cold|r-)' host/desktop/ui/app.css` prints nothing.

- [ ] **Step 6: Visual check**

`node $S/preview/preview.mjs $S/preview/t1 all both 880x620` → `rendered without page errors`. Read a sample (home-running, install-running, uninstall-confirm in light and dark): Archivo renders (not a system fallback), buttons are pills, dark mode has no light-on-light text.

- [ ] **Step 7: Commit** — `git add -A host/desktop/ui tests/host/desktop/test_ui_assets.py && git commit -m "desktop: design-system tokens, fonts and shared styles"`

---

### Task 2: Home screens (frames 7b running, 7c stopped, 7d didn't start; not installed)

**Files:**
- Modify: `host/desktop/ui/index.html` templates `home:running`, `home:stopped`, `home:not_installed`, `home:wrong`
- Modify: `host/desktop/ui/app.css` (`.hero`, `.pan*`, `.row`, `.update-note`, `.odds`, `.tiles`, new error card)
- Test: `tests/host/desktop/test_ui_assets.py` (existing tests only)

**Interfaces:**
- Consumes: Task 1 tokens, `.btn-*`, `.badge-*`, `--i-*` icons.
- Produces: `.hero` / `.pan` / `.odds` layout reused by Task 4 for the state screens (unreachable, unresponsive, runtime update).

Design values (7b–7d, scale to 880×620): content padding 0 40px 28px; hero vertically centred, `display:flex; gap:32px`; pan 140–180px circle; text column badge → `.display` → `.lede` with 12px gaps; action row aligned to the text column's left edge (design: `padding-left: pan width + gap`); bottom block `border-top:1px solid var(--line); padding-top:16px` with `.overline` "Odds and ends" left and the version in mono 12px `--ink-3` right, then the four tiles as **ghost** 32px pill buttons with 16px icons, `margin-left:-12px`, `gap:4px`.

Pans (CSS only, `aria-hidden`): running = `--gold-soft` ring, inner disc `--code-bg`, white-ish blob `--code-ink`, gold yolk with `inset -4px -4px 0 var(--orange)`; stopped = `--hover` ring, `--paper` disc with `inset 0 0 0 1px var(--line)`, egg `--raised` with `inset 0 0 0 1px var(--line-strong), inset -5px -5px 0 var(--line)`; wrong = stopped pan on a `--danger-soft` ring plus a 40px `--danger` disc bottom-right holding the alert-triangle icon in `--on-danger`, ringed `0 0 0 4px var(--page)`; not installed = stopped ring with an empty pan (no egg). Exact sizes from `$S/design/v3.html` 7b–7d.

- [ ] **Step 1: Restructure the four templates**

Keep every existing element, `data-action`, `data-field`, `data-when` and copy. Buttons gain icon spans: Open Eggie (primary, `external` after the label), Stop the kitchen (secondary, `stop`), Start the kitchen (primary, `play`), Set up the kitchen (primary, `play`), Run Doctor (primary on wrong, `activity`), Restart the kitchen (secondary, `refresh`). Badges get an icon: running `check-circle`, wrong `x-circle`. The `.update-note` becomes a gold alert: `--gold-soft` fill, 12px radius, info icon, bold title + `--ink-2` line, Update now as a 28px secondary pill on the right. On `home:wrong` replace `<details class="log">…</details>` with a paper card (`--paper`, 1px `--line`, 12px radius, `--shadow-sm`, padding 12px 12px 12px 16px, max-width 640px, aligned with the action row) holding `<pre data-field="problem">` in mono 13px (`white-space:pre-wrap; overflow-wrap:anywhere; max-height:96px; overflow:auto`) and a ghost 28px "Copy log" button with the copy icon and `data-action="copy-log"`. `copy-log` finds its text via `closest('details.log, section, #notice').querySelector('pre')` — the card must be inside the screen's `<section>` and contain the only `<pre>` before any other in that section.

- [ ] **Step 2: Style it in `app.css`**

Per the values above. The version line is `.tile-note` today — move it into the odds header row (keep the exact `Version <span data-field="app_version"></span><span data-when="runtime_version"> &middot; <span data-field="runtime_version"></span></span>` markup on one line; the tests parse it line by line).

- [ ] **Step 3: Run tests — expect PASS**

`TMPDIR=/home/ihor/tmp python3 -m pytest tests/host/desktop -q`

- [ ] **Step 4: Visual check against the design**

`node $S/preview/preview.mjs $S/preview/t2 home-running,home-running-update,home-stopped,home-not-installed,home-wrong both 880x620,800x560` → no page errors, no overflow. Read the PNGs next to 7b–7d in `v3.html`: hero, badge, title size, action row, odds footer match in proportion; at 800×560 the primary button and all four tiles are visible; home-wrong's 30-line problem scrolls inside its card; "Copy log" visible.

- [ ] **Step 5: Commit** — `git commit -am "desktop: home screens follow the design system"`

---

### Task 3: Install (frame 7a) and first run

**Files:**
- Modify: `host/desktop/ui/index.html` templates `install:running`, `install:reboot`, `install:failed`, `first-run`
- Modify: `host/desktop/ui/app.js` — `ACTIONS['start-install']` row markup, `window.eggie.handlers.install` (download fraction), `swap()` (carry counter + bar)
- Modify: `host/desktop/ui/app.css` (`.install*`, `.rows-col`, `.rows`, `.row-step*`, `.bar`, `.download-note`, brand panel, `.steps`)
- Test: `tests/host/desktop/test_ui_assets.py` (existing tests only)

**Interfaces:**
- Consumes: Task 1 tokens/icons/classes.
- Keeps (JS contracts other code and tests rely on): `[data-rows]` list, `li.row-step[data-status][data-name]` with `.label` and `.state` children, `STATE_WORDS` unchanged, `[data-field="counter"]`, `[data-field="progress"]` width set from `window.eggie.done / window.eggie.total`, `Math.min(` clamp, `[data-retry]` removed only under the `dead_end` branch, `.install-failed details.log` with the toggle-to-bottom listener, `data-when="resumed"` line containing the exact `RESUME_NOTICE` sentence.

Layout (7a, scaled): `display:grid; grid-template-columns: 300px minmax(0,1fr); height:100%`. Left brand panel `--gold-soft`, right hairline border, `overflow:hidden`, `aria-hidden`: wordmark (`.eg-brand-mark`-style 20px egg + "eggie", wide 650 18px) top-left at 28px; composition scaled ~0.8 from 7a — big gold egg (`inset -16px -16px 0 var(--orange)`), coral / periwinkle / orchid discs, accent pill "ubuntu 24.04" in mono 12px `--on-accent`. Variants: `install:reboot` panel uses `--hover` with muted shapes; `install:failed` uses `--danger-soft`. First run uses the gold panel. No caption text in the panel (copy stays as today).

Right column: `display:flex; flex-direction:column; min-height:0; padding:40px 40px 24px`. Fixed header: `.overline` holding `[data-field="counter"]`, under it the overall bar (`.bar` → 6px pill track `--hover` + `inset 0 0 0 1px var(--line)`, fill `--accent`, max-width 320px), `.title` (wide, 30px), `.lede`, `.hint`s, then the screen's buttons in a row (reboot: Restart now primary + I'll restart later secondary; failed: Try this step again primary `refresh` icon + Start over from scratch secondary, then the collapsed `details.log`). Then the steps card: `flex:1; min-height:0; overflow:auto`, `--paper`, 1px `--line`, 12px radius, `--shadow-sm`. Only the card scrolls.

Rows: `li.row-step` grid `20px 1fr auto`, gap 12px, padding 10px 16px, min-height 48px, hairline between rows; label 14px 500 `--ink`; `.state` caption 12px. Status visuals: `waiting` = 16px ring `inset 0 0 0 1.5px var(--line-strong)`, caption `--ink-3`; `running` = `--raised` row background, 16px accent spinner (border spinner, `eg-spin` 0.8s), label 600, caption `--accent-ink`; `done`/`skipped` = success check-circle icon, caption `--ink-3`; `failed` = `--danger-soft` row, danger x-circle, caption `--danger`; `reboot` = `--warning-soft` row, warning alert-triangle, caption `--warning`.

- [ ] **Step 1: Rebuild the four templates** per the layout; `first-run` = same grid, right column holds today's title row, lede, the three-step `ol.steps` as a card (numbered 24px `--gold-soft`/`--gold-ink` discs), and the primary button + hint. Remove the `.download-note` element.

- [ ] **Step 2: Move the download fraction into its row in `app.js`**

In `ACTIONS['start-install']` each `li` also gets a hidden `<div class="row-progress"><span class="track"><span class="fill"></span></span><span class="pct"></span></div>` spanning the label column. In the install handler, replace the `[data-field="download"]` note block: when `event.fraction` is a number, show that row's `.row-progress`, set `.fill` width and `.pct` text to `${Math.round(event.fraction * 100)}%` (mono 12px `--ink-2`); hide it on any later status of that row. Keep the overall bar code and its comment about not binding the bar to `fraction`.

- [ ] **Step 3: Keep counter and bar across `swap()`**

Before `show()` in `swap()`, read the current counter text and bar width; after the rows are spliced back, write them into the new screen's `[data-field="counter"]` / `[data-field="progress"]`.

- [ ] **Step 4: Run tests — expect PASS**

`TMPDIR=/home/ihor/tmp python3 -m pytest tests/host/desktop -q`

- [ ] **Step 5: Visual check**

`node $S/preview/preview.mjs $S/preview/t3 install-running,install-reboot,install-failed,first-run both 880x620,800x560` → no errors/overflow. Read PNGs vs 7a: brand panel, header, card. Check: "Step 4 of 9" and the bar show on all three install scenarios; the download row shows 54% with its bar; at 800×560 Restart now / Try this step again are visible and only the card scrolls; reboot and failed rows show their warning/danger styles.

- [ ] **Step 6: Commit** — `git commit -am "desktop: install and first-run screens follow the design system"`

---

### Task 4: Remaining screens

**Files:**
- Modify: `host/desktop/ui/index.html` templates `unreachable`, `unresponsive`, `runtime-update:running`, `runtime-update:failed`, `recover-confirm`, `updates`, `app-update:running`, `settings`, `quit-confirm`, `quitting`, `doctor`, `uninstall-confirm`
- Modify: `host/desktop/ui/app.css`
- Test: `tests/host/desktop/test_ui_assets.py` (existing tests only)

**Interfaces:**
- Consumes: Task 1 classes/icons, Task 2 `.hero` / `.pan` (state screens may reuse the stopped or wrong pan).
- Keeps: `data-setting="autostart"` checkbox with `data-action="toggle-autostart"`, `input[name="purge"]` (not pre-checked), `[data-field="fraction"]` bar width in app-update, every `data-action`.

- [ ] **Step 1: Restyle per the spec's "Other screens"**

Left-aligned page, padding 40px, max text width 560px: badge (icon + word) → `.title` or `.display` → `.lede` → actions row → `details.log`. unreachable (warning badge) / unresponsive (danger badge) / runtime-update failed (danger badge) may use the Task 2 hero with the stopped/wrong pan; runtime-update running gets a `--gold-soft` pan with an accent spinner. recover-confirm: `.warn` becomes a warning alert (warning-soft, alert-triangle). settings: the checkbox becomes a design-system switch (36×20 pill, accent when checked) — keep the `<input type="checkbox">`; Back as secondary. uninstall-confirm: purge as a design-system checkbox with `.check-desc`-style description, `.warn` as a danger alert with alert-triangle, Remove Eggie as `.btn-danger` (`trash` icon), Keep it secondary. doctor: title, the log as the dark code block filling the height (`flex:1; min-height:0`), Done + Copy log. updates / app-update: card-free centred column; app-update bar = 6px accent track. quit-confirm / quitting: centred; quitting gets an accent spinner.

- [ ] **Step 2: Run tests — expect PASS**

`TMPDIR=/home/ihor/tmp python3 -m pytest tests/host/desktop -q`

- [ ] **Step 3: Visual check**

`node $S/preview/preview.mjs $S/preview/t4 unreachable,unresponsive,runtime-update-running,runtime-update-failed,recover-confirm,updates-available,updates-none,app-update-running,settings,quit-confirm,quitting,doctor,uninstall-confirm,notice both 880x620,800x560` → no errors/overflow. Read PNGs: primary/danger buttons visible at 800×560; doctor's 30-line log scrolls in its block; the notice bar reads as a danger alert and doesn't cover the page's primary button at 880×620.

- [ ] **Step 4: Commit** — `git commit -am "desktop: remaining screens follow the design system"`

---

### Task 5: Native title bar recolour

**Files:**
- Create: `host/providers/title_bar_win.py`
- Modify: `host/providers/desktop_win.py`, `host/providers/desktop_mac.py`, `host/core/provider.py` (`DesktopPlatform`), `host/desktop/__main__.py` (`run()`)
- Modify: `tests/host/test_provider_surface.py` (`DESKTOP_SURFACE` gains `"style_title_bar"`), `tests/host/desktop/test_entry.py` (`FakeDesktop.style_title_bar`, `FakeEvents.before_show`)
- Create: `tests/host/test_title_bar_win.py`
- Modify: `host/desktop/CLAUDE.md`

**Interfaces:**
- Consumes: Task 1's `app.css` token blocks (`--raised`, `--ink-2`, `--line`, light `:root` and dark media block).
- Produces:
  - `title_bar_win.COLORS: dict[str, dict[str, str]]` = `{"light": {"caption": "#fefbf7", "text": "#6a5848", "border": "#efe6d9"}, "dark": {"caption": "#211a14", "text": "#cdb9a4", "border": "#3a2f25"}}`
  - `title_bar_win.colorref(hex_colour: str) -> int` — `"#RRGGBB"` → `0x00BBGGRR`
  - `title_bar_win.apply(hwnd: int, dark: bool, set_attribute: Callable[[int, int, int], None]) -> None` — calls `set_attribute(hwnd, attr, value)` for `DWMWA_USE_IMMERSIVE_DARK_MODE = 20` (value `1` if dark else `0`), `DWMWA_BORDER_COLOR = 34`, `DWMWA_CAPTION_COLOR = 35`, `DWMWA_TEXT_COLOR = 36` with the `colorref` of that theme's colours. Each attribute is attempted independently; a raise from one is caught and logged, the rest still run.
  - `title_bar_win.dwm_set_attribute(hwnd, attr, value) -> None` — `ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, byref(c_int(value)), sizeof(c_int))`; raises `OSError` on a non-zero HRESULT.
  - `title_bar_win.os_uses_dark() -> bool` — `AppsUseLightTheme` under `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize` is `0`; any read failure → `False`.
  - `DesktopPlatform.style_title_bar(window) -> None`. `WindowsDesktop.style_title_bar`: `hwnd = int(window.native.Handle.ToInt64())`, then `apply(hwnd, os_uses_dark(), dwm_set_attribute)`; any exception logged as a warning, never raised. `MacDesktop.style_title_bar`: no-op with a one-line docstring.
  - `run()`: after `create(...)` succeeds, `window.events.before_show += lambda: desktop.style_title_bar(window)` (pywebview ≥5 has `before_show`; the handler runs once the native form exists).

- [ ] **Step 1: Write the failing tests** in `tests/host/test_title_bar_win.py` (pure — no ctypes, no Windows needed):

```python
def test_colorref_is_bgr():
    assert colorref("#fefbf7") == 0x00F7FBFE
    assert colorref("#2A1F16") == 0x00161F2A

def test_light_title_bar_sets_caption_text_border_and_light_buttons():
    calls = []
    apply(7, False, lambda h, a, v: calls.append((h, a, v)))
    assert sorted(calls) == sorted([(7, 20, 0), (7, 34, 0x00D9E6EF), (7, 35, 0x00F7FBFE), (7, 36, 0x0048586A)])

def test_dark_title_bar_uses_dark_tokens_and_dark_buttons():
    calls = []
    apply(7, True, lambda h, a, v: calls.append((a, v)))
    assert dict(calls) == {20: 1, 34: colorref("#3a2f25"), 35: colorref("#211a14"), 36: colorref("#cdb9a4")}

def test_an_unsupported_attribute_does_not_stop_the_others():
    # Windows 10 rejects caption/text/border colours; dark mode must still apply.
    seen = []
    def setter(h, a, v):
        seen.append(a)
        if a in (34, 35, 36): raise OSError("E_INVALIDARG")
    apply(7, True, setter)
    assert sorted(seen) == [20, 34, 35, 36]

def test_the_title_bar_colours_match_the_page_tokens():
    # Reads --raised, --ink-2, --line from host/desktop/ui/app.css (via __file__):
    # the :root block and the block inside @media (prefers-color-scheme: dark).
    # Asserts COLORS["light"] == {caption: --raised, text: --ink-2, border: --line} and same for dark.
    # Assert both blocks were found (no vacuous pass).
```

Also: in `tests/host/desktop/test_entry.py` add `test_the_title_bar_is_styled_before_the_window_shows` — after `run(...)` with the fakes, `window.events.before_show.handlers` has one handler, calling it records the window on `FakeDesktop.styled`. And `test_a_title_bar_failure_does_not_stop_launch` for `WindowsDesktop.style_title_bar` given an object without `.native` → returns `None`, no raise (in `tests/host/test_desktop_win.py`).

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: host.providers.title_bar_win`, missing `before_show`, surface mismatch)

`TMPDIR=/home/ihor/tmp python3 -m pytest tests/host/test_title_bar_win.py tests/host/desktop/test_entry.py tests/host/test_desktop_win.py tests/host/test_provider_surface.py -q`

- [ ] **Step 3: Implement** the module, both platform methods, the protocol line, the `run()` hook, `DESKTOP_SURFACE` update and fakes, per Interfaces. Import `ctypes`/`winreg` inside the functions that need them so the module imports on Linux/macOS (tests run on Linux).

- [ ] **Step 4: Run the full suite — expect PASS**

`TMPDIR=/home/ihor/tmp python3 -m pytest -q` (includes `test_no_platform_leak.py`, `test_constants_agree.py`, `test_no_dead_modules.py`).

- [ ] **Step 5: Document** in `host/desktop/CLAUDE.md` under "Things that will bite you": the title bar is recoloured by `DesktopPlatform.style_title_bar` on `events.before_show`; DWM caption/text/border colours need Windows 11 (Windows 10 keeps the default bar, dark-mode buttons still apply); colours are read once per launch; `title_bar_win.COLORS` is held equal to `app.css` by a test — change both together.

- [ ] **Step 6: Commit** — `git add -A host tests && git commit -m "desktop: recolour the Windows title bar to the design system"`

---

## Not covered by automated tests (note in the PR)

- The real DWM call and pywebview's `before_show` on Windows — needs a Windows 11 run (`docs/release-testing.md` style manual check). Linux CI can't exercise ctypes.windll.
- Pixel fidelity — checked by the headless harness screenshots, not asserted.
