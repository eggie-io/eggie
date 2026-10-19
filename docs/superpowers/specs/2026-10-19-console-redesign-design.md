# Console redesign — design

Issue: eggie-io/eggie-resources#94. Scope: `runtime/web/` only (the browser console). Nothing in
`host/`, the API, or the host↔VM seam changes.

## Why

The console still wears the first board's look (Bricolage/Hanken/Plex, yolk/basil/paprika, 11px
radii). The Eggie design system is settled (claude.ai/design project "Omelet: Linux VM Setup UI",
file `Eggie Host + Console v3.dc.html`, console frames 7e–7k, design system under
`_ds/eggie-console-design-system-…/`), and the host was moved to it in eggie#85. The console is also
laid out for a small window: project, files and secrets pages sit in a ~880px column in the middle
of a wide browser. The user expects some of the console's visual bugs to disappear with the
restyle, and did not list any to fix separately.

## Goals

- Every console screen uses the design system: tokens, Archivo + JetBrains Mono, pill buttons,
  status dots, badges, cards, alerts, dialogs, the 48-glyph stroke icon set.
- Frames 7e (projects), 7f (project overview), 7i (files), 7j (agents), 7k (connect an agent) are
  matched closely. Screens the design does not draw (sign-in, signed out, not answering, needs
  update, wrong host, onboarding, secrets, every dialog, starting / wrong / waiting / gone project
  states, uploads) are restyled in the same language.
- The console works at full browser width: pages use the width up to the design system's
  `--content-max` (1200px), and a project gets the 240px **project menu** from 7f/7i.
- It still works in the desktop window (880×620, min 800×560) and on a phone-width browser.

## Non-goals (decided)

- **No new behaviour, no API changes.** The design draws things the console cannot do: the
  **Changes** page and its menu item, **Roll back** (7h), GitHub sync status (Synced / Not synced),
  the GitHub card with recent changes on the overview, "Sync now". All left out.
- The design's 32px title bar with window controls is the native window's, not the page's. Not
  drawn.
- No theme toggle: the theme follows the OS (`prefers-color-scheme`), as today.
- No sharing of code or assets with `host/desktop/ui/` (existing rule).

## Decisions

- **Shell (7e):** a 48px kitchen bar on `raised` — back-to-Eggie icon button (only when the desktop
  `home=` is known, as today), egg brand mark, instance switcher as a ghost button in wide 650 15px
  type, a "Open" status dot, account avatar menu on the right — then a Tabs row: Projects (layers
  icon, project count) and Agents (terminal icon), accent underline on the current one. During
  onboarding the tabs row shows the onboarding progress instead, as today.
- **Project menu (7f/7i):** `/p/:id`, `/p/:id/files/*` and `/p/:id/secrets` share one layout: a
  240px `raised` column with a hairline right border — "All projects" ghost button, a project tile
  (32px letter square in the project's colour + name + status dot), then items Overview · Analyze ·
  Files · Secrets · Public address, and Delete project pinned at the bottom in `danger`. Analyze,
  Public address and Delete open the dialogs they open today. Public address keeps today's rules:
  hidden on a site, shown disabled (`ink-3`, note) when unavailable, its note is today's tile line.
  Secrets shows "N requested" as its note when there are requests.
- **Project colour:** the letter square's colour is picked from coral, gold, periwinkle, orchid by
  a stable hash of the project id, so a project keeps its colour across reloads.
- **Width:** list and agents pages are centred and capped at 1200px. In project pages the menu sits
  at the left edge and the content beside it is capped at 1200px. Below 760px the project menu
  becomes a horizontally scrolling row of items above the content.
- **Copy** stays as it is today everywhere, except labels the design changes for buttons the app
  already has (e.g. the row's gear "Settings" becomes an icon button labelled "Project settings").

## Constraints

- Works offline: fonts ship as files (`@fontsource-variable/archivo` with the width axis,
  `@fontsource-variable/jetbrains-mono`); `check-offline` must stay green; the CSP refuses `data:`
  fonts.
- The kit (`packages/ui`) may import only React, its fonts and its own files
  (`packages/ui/test/boundary.test.ts`).
- Shared `om-*` keyframes are referenced as `global(om-…)` from module CSS
  (`packages/ui/test/animations.test.ts`).
- All existing `npm test` suites, `npm run typecheck`, `npm run build && npm run check-offline`
  stay green.

## Tests

No JS UI test runner beyond Vitest on logic. One new invariant test: every `var(--name)` used in
the kit and app CSS is defined in `tokens.css` (or declared in the same file), so a token renamed
by the redesign cannot silently fall back to nothing. Visual checks are headless screenshots
(Playwright + Chrome) of the dev app's `?scenario=` states at 1440×900, 880×620 and 390×844, light
and dark.

## Left to the builder

Exact class names, kit component internals, how the old screens' CSS modules are folded, the
narrow-width details beyond the rules above.
