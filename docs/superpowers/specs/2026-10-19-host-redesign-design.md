# Host desktop redesign — design

Issue: eggie-io/eggie-resources#93. Scope: `host/` only. Nothing under `runtime/` (the console) changes.

## Why

The Eggie design system is settled (claude.ai/design project "Omelet: Linux VM Setup UI", file
`Eggie Host + Console v3.dc.html`, frames 7a–7d). The console already follows it; the host window
still uses the older board (Bricolage/Hanken fonts, yolk/basil/paprika palette, big rounded
buttons). The host should look like the same product as the console it opens.

## Goals

- Restyle every screen in `host/desktop/ui/` to the design system: tokens, fonts, buttons, badges,
  cards, alerts, icons.
- Match frames 7a (install), 7b (running), 7c (stopped), 7d (didn't start) closely.
- Restyle the screens the design does not draw (first run, not installed, unreachable,
  unresponsive, runtime update, recover confirm, updates, app update, settings, quit confirm,
  quitting, doctor, uninstall confirm, the crash notice, the busy strip) in the same language.
- Recolour the native window title bar to the design's title-bar colours on Windows.

## Non-goals (decided by the user)

- **No new behaviour.** Same screens, same buttons, same actions, same `data-action` names, same
  `DesktopApi` surface. The design is a visual reference only. Elements the design shows that the
  app does not do today are left out: install **Cancel** and **Run in background**, the install
  folder path footer, the error timestamp ("Today, 14:52 · after 38 seconds"), Settings/Doctor
  shown as dialogs, etc.
- **No custom-drawn title bar / frameless window.** The same window shows the console from the VM;
  a page-drawn title bar would vanish there. The native title bar stays, recoloured.
- **Window stays resizable and maximizable.** The issue asked to forbid expanding; the user
  dropped that — in the console people want a bigger window.
- No changes to the console, runtime, or the host↔VM seam.

## Anti-goals

- Fetching anything remote. The UI must work offline; `test_ui_assets.py` pins this and the CSP.
- Platform branching outside `host/providers/` (`tests/test_no_platform_leak.py`).

## Constraints

- `ui/index.html` CSP stays exactly as strict (`default-src 'self'; script-src 'self' 'unsafe-eval'`
  …). Inline SVG markup is fine; no inline `<script>`.
- Window size stays 880×620, min 800×560 (`__main__.py`). The design frames are 1112×790, so
  spacing is scaled down; nothing may clip at 800×560 (the install rows column may scroll).
- Theme follows the OS via `prefers-color-scheme`, as today. The design system's dark values
  (its `:root[data-theme="dark"]` block) go under that media query.
- `pywebview` injects `body { user-select:none }`; log/pre blocks keep opting back in.

## Visual system (from the design system)

Reference copies are in this session's scratchpad (`design/ds/ds-reference.css`, `design/v3.html`,
`design/ds/fonts/`, `design/ds/_ds_bundle.js` → `iconPaths`); the source of truth is the
claude.ai/design project above.

- **Tokens:** replace the old palette in `app.css` with the design-system tokens verbatim:
  surfaces `--page #fcf7f0`, `--raised #fefbf7`, `--paper #fff`, `--hover #f7f0e5`; lines `--line
  #efe6d9`, `--line-strong`, `--line-input`; ink `--ink #2a1f16`, `--ink-2 #6a5848`, `--ink-3`;
  brand `--gold #ffc21a`, `--orange`, `--coral`, `--periwinkle`, `--orchid`, `--gold-soft`;
  `--accent #e0662f` (focus, links, progress fill, checked controls); status success / warning /
  danger / info strong+soft pairs; radii (`pill` for buttons/badges/progress, 12px cards, 20px
  dialogs); warm shadows; 4px spacing grid. Dark values as listed in the design system.
- **Fonts:** Archivo (variable, weight + width axes) for all UI, JetBrains Mono for versions, logs,
  progress numbers. Titles and display set wide (`font-variation-settings:"wdth" 112`, weight 650,
  negative tracking). Ship the two woff2 files from the design system; delete the Bricolage,
  Hanken and Plex files. `fonts/OFL.txt` carries the OFL 1.1 text with both projects' copyright
  lines (Archivo: The Archivo Project Authors; JetBrains Mono: The JetBrains Mono Project Authors).
- **Components (as plain CSS classes, no React):** pill buttons (primary gold → orange + glow on
  hover; secondary paper + `line-input` border; ghost; danger), sizes 28/32/40px; badges
  (success / outline / danger / gold / warning) with a 14px icon; card (`paper`, 1px line,
  shadow-sm, 12px); alert (soft fill + 20px icon); progress track 6px with accent fill; checkbox
  and switch; dark code block for logs.
- **Icons:** the design system's stroke set (24px grid, 1.75 stroke, round caps), drawn in
  `currentColor`. *Builder's call:* keep the existing mechanism — a CSS `mask` from an `--icon`
  custom property holding a `data:image/svg+xml` URI built from the design system's path data
  (`iconPaths` in the bundle) — extended from the tiles to every button/badge/row that carries an
  icon. It keeps the markup clean and `test_every_home_tile_has_an_icon` unchanged. Glyphs the host
  uses: play, stop, external, refresh, activity, download, settings, trash, copy, check-circle,
  x-circle, alert-triangle, info. Only those are copied.

## Screens

**Home states (7b running, 7c stopped, 7d didn't start; not-installed in the same pattern).**
Vertically centred hero: 180px "pan" circle illustration (7b: gold-soft ring, dark pan, gold yolk;
7c: hover ring, paper pan, outlined egg; 7d: danger-soft ring + red alert-triangle disc;
not-installed: hover ring, empty paper pan), then a column with badge → wide 650 title → `ink-2`
lede. Actions sit in a row aligned under the text column (design indents 220px), `lg` buttons with
icons: Open Eggie (primary, external icon) / Stop the kitchen (secondary, stop icon); Start the
kitchen (primary, play icon) and the existing disabled Open Eggie + hint; Run Doctor / Restart the
kitchen on "wrong". Bottom: hairline-topped "Odds and ends" block — overline label left, version
in mono `ink-3` right (keeps the existing `data-when="runtime_version"` gating), then ghost
buttons with icons: Doctor (activity), Check for updates (download), Settings (settings),
Uninstall (trash). The app-update note becomes a design-system alert (gold or info tone) with its
Update now button, placed between hero and actions. On "wrong", the problem text goes in a paper
card like 7d's error card: mono text (scrolls if long) + a small ghost "Copy log" button with the
copy icon; it replaces the `<details>` disclosure there.

Copy stays as it is today on every screen.

**Install (7a), and its reboot/failed variants.** Two columns: a fixed-width left brand panel
(design: 380px at 1112 wide; scale to fit) on `gold-soft` with a hairline right border — "eggie"
wordmark with the egg mark top-left, the flat geometric composition (gold egg with orange inset,
coral/periwinkle/orchid discs, accent "ubuntu 24.04" pill). Reboot variant uses the neutral
palette, failed uses danger-soft. Right column: overline "Step N of M" (the existing counter), wide
title (existing copy: "Heating the pan" / "Time for a quick nap" / "The burner wouldn't light"),
lede and hints, then the steps as one card: one row per step, hairline-separated. Every row keeps
today's caption words on the right (Waiting / Working… / Done / Didn't work / Needs restart).
Row states: done and skipped = success check-circle; running = accent spinner, `raised`
background, and — on the step that reports a fraction (download) — a progress bar with the percent
in mono under the label (replacing the separate download footer line); waiting = empty
`line-strong` ring, muted caption; failed = danger x-circle on danger-soft; reboot = warning
alert-triangle on warning-soft. The overall progress bar stays (it is driven by completed steps and
pinned by a test): a thin accent track under the overline/counter.

*Builder's call on layout:* the right column is a fixed header (overline + overall bar, title,
lede/hints, then the screen's action buttons and — on failed — the collapsed log) followed by the
steps card, which takes the remaining height and scrolls on its own. Buttons therefore never leave
the viewport at 800×560. On reboot and failed, `swap()` carries the "Step N of M" overline and bar
over (or recomputes them) so they still show.

**First run.** Same two-column shell as install (brand panel left); right column holds the
existing welcome copy, the three numbered steps as a card, and "Warm up the kitchen" + hint.

**Other screens** (unreachable, unresponsive, runtime update running/failed, recover confirm,
updates, app update, settings, quit confirm, quitting, doctor, uninstall confirm): left-aligned
page — badge (where there is one) → wide title → lede → actions → log. *Builder's call:* logs stay
collapsed `<details class="log">` disclosures (as today, so the error screens fit at 800×560 and
`app.js`'s toggle/copy wiring and `test_the_install_failed_screen_shows_the_error_it_points_to`
keep working); the opened content is the dark code block with a ghost "Copy log" button. Only
`home:wrong` shows its problem text as an always-visible card, per 7d. Settings' autostart checkbox becomes a design-system switch.
Uninstall's purge option becomes a design-system checkbox with description; its warning a danger
alert; Remove Eggie a danger button. Recover confirm's warning is a warning alert.

**Crash notice (`#notice`)** stays fixed to the bottom edge, styled as a danger alert bar.
**Busy strip (`#busy`)** stays a 3px strip, accent colour. The pressed-button spinner stays.

## Native title bar recolour

- New `DesktopPlatform` method, e.g. `style_title_bar(window)`, on both platform objects in
  `host/providers/`. `run()` calls it once the native window exists (pywebview `events.before_show`
  if available in the pinned range, otherwise `events.shown`). Any failure is logged as a warning
  and never stops the app.
- **Windows:** `DwmSetWindowAttribute` on the form's HWND: caption colour = `--raised`, caption text
  = `--ink-2`, border = `--line`; in dark mode also `DWMWA_USE_IMMERSIVE_DARK_MODE` so the system
  buttons draw light, with the dark values of the same tokens. Light/dark comes from the OS
  setting (`AppsUseLightTheme`). Caption colour needs Windows 11; on Windows 10 the call fails and
  the default title bar stays — acceptable. Colours are applied once per launch; an OS theme
  switch while the app runs takes effect on the next launch (the page itself switches at once).
- **macOS:** no-op for now (mac is mostly unverified; the native title bar stays).
- The colours live in one Python table; a test holds them equal to the `--raised`, `--ink-2` and
  `--line` values in `app.css` (light and dark), so the two cannot drift.

## Tests

Following the repo's testing rules — only tests that can catch a real bug:

- Update `test_the_three_typefaces_are_bundled` to the two new families.
- Title-bar colours: hex → COLORREF conversion (`0x00BBGGRR` byte order is easy to get backwards);
  the DWM attributes and values passed for light and dark through an injected setter; a failing
  setter does not raise.
- The Python colour table agrees with `app.css` tokens.
- Every other existing `test_ui_assets.py` invariant keeps passing unchanged (offline, CSP,
  templates, version gating, no platform names in copy, tile icons, install log, overall bar,
  step counter clamp, retry removal).

No JS test runner exists; visual checks are done by rendering the page headlessly
(Playwright + Chrome are available here) at 880×620 and 800×560 in light and dark, with the
bridge stubbed, and comparing against the design frames.

## Left to the builder

Exact pixel sizes after scaling to 880×620, how the pan illustrations are built in CSS, class
names, how `app.js` builds the step rows (keeping its existing behaviour), and the order of work.

## Docs

`host/desktop/CLAUDE.md`: note the title-bar recolour (where it lives, Windows-11-only, applied
once per launch). Remove the "OS draws the title bar; the board's mock chrome is not reproduced"
comment's stale board references in `app.css`.
