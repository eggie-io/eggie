# Night-sky Dark Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the warm-brown dark palette with the night-sky navy/indigo one from the design, in the console (also served by kitchens) and the desktop app.

**Architecture:** Pure token swap. Dark values live in three places: `runtime/web/packages/ui/src/tokens.css` (twice: the `prefers-color-scheme` block and `.om-theme-dark`), `host/desktop/ui/app.css`, and `host/providers/title_bar_win.py` (Windows title bar, held equal to `app.css` by a test). Components use tokens only, so nothing else changes.

**Tech Stack:** CSS custom properties, Python 3.12 (pytest), Vitest.

**Spec:** eggie-io/eggie-resources#120 + design file `Eggie Host + Console v3.dc.html`, its `:root[data-theme="dark"]` style block (values copied below).

## Global Constraints

- Light theme does not change. Gold (`--gold`, `--orange`, `--on-gold`) and the accent (`--accent`, `--accent-hover`, `--accent-ink`) keep their values.
- New dark values (from the design, verbatim):
  - `--page: #15142a; --raised: #1c1a33; --paper: #24223f; --hover: #2d2a4b;`
  - `--line: #332f52; --line-strong: #4a456f; --line-input: #6f6a96;`
  - `--ink: #f3f1fb; --ink-2: #bdb8d8; --ink-3: #8f8ab0; --on-ink: #1c1a33; --on-ink-2: #4a456f;`
  - `--gold-soft: #3a3122; --gold-ink: #ffd36b;`
  - `--accent-soft: #3a2836; --on-accent: #1c1a33;`
  - `--success-soft: #1a3133; --warning-soft: #3a3122; --danger-soft: #3d1f33; --on-danger: #1c1a33; --info-soft: #262a55;`
  - `--code-bg: #100f22; --code-ink: #eeecfa; --code-ink-2: #a9a4c8;`
  - `--scrim: rgba(6, 5, 20, 0.64);`
  - `--shadow-sm: 0 1px 2px rgba(5, 4, 20, 0.35);`
  - `--shadow-md: 0 8px 22px -8px rgba(5, 4, 20, 0.6), 0 1px 3px rgba(5, 4, 20, 0.3);`
  - `--shadow-lg: 0 30px 60px -24px rgba(5, 4, 20, 0.75), 0 2px 6px rgba(5, 4, 20, 0.3);`
- Dark tokens the design leaves alone keep today's dark values: `--success #6fd39a`, `--warning #ffb547`, `--danger #ff8a84`, `--danger-hover #ffa29d`, `--info #9aa6ff`, `--accent #ff9a66`, `--accent-hover #ffb088`, `--accent-ink #ffc2a0`.
- `--focus` / `--selected` stay aliases of `--accent` / `--accent-soft` (the design's literal values equal those).
- Desktop `app.css` declares only the tokens it already declares; don't add console-only ones (`--on-ink`, `--scrim`).
- Tests: `TMPDIR=/home/ihor/tmp python3 -m pytest -q` (Python); `npm test` in `runtime/web` (Vitest).

## Review Focus

- The two dark blocks in `tokens.css` drift apart (one updated, one forgotten) → `/kit` gallery and the real app disagree. Pinned by the Task 1 test.
- Desktop and console dark palettes drift apart → the desktop app and the console it opens look like two products. Pinned by the Task 2 test.
- A brown value survives in a dark block (e.g. `--on-accent` still `#2a1f16`). Covered by the equality tests plus the reviewer grepping the old hexes.
- `--code-ink` / `--code-ink-2` were never set in the console dark blocks; they must be now, or code text stays cream on navy.
- Windows title bar keeps the brown caption above a navy page. Pinned by the existing `test_the_title_bar_colours_match_the_page_tokens`.

---

### Task 1: Console dark tokens

**Files:**
- Modify: `runtime/web/packages/ui/src/tokens.css:23-53` (both dark blocks)
- Test: `runtime/web/packages/ui/test/tokens.test.ts`

**Interfaces:**
- Produces: the console dark token set, which Task 2's test compares against.

- [ ] **Step 1: Write the failing test** in `tokens.test.ts`, `"the OS dark block and the .om-theme-dark wrapper declare the same values"`: parse the `@media (prefers-color-scheme: dark) { :root { … } }` body and the `.om-theme-dark { … }` body into `{name: value}` maps; assert the maps are equal and that `page` is `#15142a` in both (so the test can't pass on two equally stale blocks).
- [ ] **Step 2: Run it** — `cd runtime/web && npx vitest run packages/ui/test/tokens.test.ts` — expect FAIL on `#15142a`.
- [ ] **Step 3: Update both dark blocks** with the Global Constraints values; add `--code-ink` and `--code-ink-2`. Keep `color-scheme: dark;` and line layout as today.
- [ ] **Step 4: Run** `cd runtime/web && npm test` — expect PASS (including the existing undeclared-`var()` test).
- [ ] **Step 5: Commit** — `git commit -m "Console: night-sky dark tokens"`

### Task 2: Desktop dark tokens and Windows title bar

**Files:**
- Modify: `host/desktop/ui/app.css:47-64` (dark block)
- Modify: `host/providers/title_bar_win.py:12` — `"dark": {"caption": "#1c1a33", "text": "#bdb8d8", "border": "#332f52"}`
- Modify: `tests/host/test_title_bar_win.py` — `test_dark_title_bar_uses_dark_tokens_and_dark_buttons` expects the new three colours
- Create: `tests/test_dark_palette_agrees.py`

**Interfaces:**
- Consumes: the console dark `:root` block from Task 1.

- [ ] **Step 1: Write the failing test** `test_desktop_dark_tokens_match_the_console` in `tests/test_dark_palette_agrees.py`: read both CSS files via `Path(__file__)`, build each side's effective dark palette (hex tokens of the light `:root` block, overlaid with the `@media (prefers-color-scheme: dark)` block — the console's dark block inherits gold/orange from `:root`), assert every token the desktop's dark block declares has the console's effective value, and assert at least 20 shared tokens were compared.
- [ ] **Step 2: Run it** — `TMPDIR=/home/ihor/tmp python3 -m pytest tests/test_dark_palette_agrees.py -q` — expect FAIL on `page`.
- [ ] **Step 3: Update `app.css`** dark block (values incl. the three shadows), `title_bar_win.py` and the hardcoded title-bar test values.
- [ ] **Step 4: Run** `TMPDIR=/home/ihor/tmp python3 -m pytest -q` — expect all PASS.
- [ ] **Step 5: Commit** — `git commit -m "Desktop: night-sky dark tokens and title bar"`
