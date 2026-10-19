# Console redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle every screen of the browser console (`runtime/web`) to the Eggie design system and give it a full-width layout with a per-project side menu.

**Architecture:** The kit (`packages/ui`) takes the design-system tokens, fonts, icon set and restyled components first, keeping its component APIs close to today's so screens pick up most of the look for free; then each screen group is reworked to the design frames. Old token names stay as temporary aliases until the last task removes them, guarded by a test that every `var(--x)` is defined.

**Tech Stack:** React 19 + TypeScript + Vite 8, CSS Modules, Vitest, react-router 8, `@fontsource-variable/*`.

**Spec:** `docs/superpowers/specs/2026-10-19-console-redesign-design.md`

## Reference material (read before your task)

Scratchpad: `R=/tmp/claude-1000/-home-ihor-projects-local-environment-for-non-tech-poc/8482b02f-79d8-467b-b79e-99a8ea8a367f/scratchpad`

- `$R/design/v3.html` — the design file. Console frames: 7e projects (line ~226), 7f overview (~287),
  7i files (~459), 7j agents (~524), 7k connect an agent (~565); sample data in the `<script>` at the
  end. `x-import …Button variant=… icon=…` = the kit Button; `…StatusDot status=…`; `…Badge tone=…`.
- `$R/design/ds/colors.css`, `sizes.css`, `ds-reference.css` (all tokens incl. typography, spacing,
  shape, dark values), `components.css` (every `.eg-*` component style — the source of truth for
  component looks), `readme.md` (visual rules: gold once per view, accent for interaction only,
  hover/focus/disabled rules, copy rules), `icons.json` (48 glyphs: name → SVG path list, 24px
  viewBox, stroke 1.75, round caps/joins, no fill).
- `$R/shoot.mjs` — headless screenshots: `cd $R && BASE=http://localhost:5179 node shoot.mjs <outdir> "name|/path?scenario=x|W|H|light-or-dark" …`.
  It sets `localStorage.eggie.onboarded=1`. Dev server for this worktree:
  `cd runtime/web/apps/console && npx vite --port 5179 --strictPort` (run in background; never
  `npm run dev -- --port`). Scenario names: `SCENARIOS` in `apps/console/src/mocks/handlers.ts`.
  Mock projects in the `ok` scenario include `recipe-box` (running, 3 addresses), `weekend-shop`,
  `tiny-crm`, `photo-sorter`, `notes-app`, `old-blog`.
- `runtime/web/CLAUDE.md` — layout of the app and "Things that will bite you" (read it).

## Global Constraints

- All commands run from `runtime/web` unless stated. Verification set: `npm test`, `npm run typecheck`, `npm run build && npm run check-offline`.
- No behaviour or API changes. Leave out Changes, Roll back, GitHub sync status, the overview GitHub card, "Sync now".
- Copy stays as today except where a task says otherwise. No emoji. Typographic apostrophes in new copy.
- Theme follows the OS: light tokens on `:root`, dark values under `@media (prefers-color-scheme: dark)` and repeated under `.om-theme-dark` (the Kit gallery wrapper), as `tokens.css` does today.
- Kit (`packages/ui/src`) imports only React, `@fontsource*` and its own files. No `fetch`.
- In `.module.css`, shared keyframes are written `animation: global(om-spin) …`.
- No hardcoded colours in TSX or module CSS — tokens only (the Egg illustration may use `--paper`/`--gold` etc.).
- Never `window.open`; external links go through `openExternal` from `desktop/desktop.ts`.
- Comments only for non-obvious edge cases; no ticket references.
- Commit after each task with a `console:` prefix, ending with the session's attribution lines.

## Review Focus

1. Long project names, long addresses and long file names: ellipsis inside their row, never a horizontal page scrollbar — at 390px wide too.
2. The desktop window at its minimum, 800×560: shell, project menu and content all fit; dialogs scroll inside themselves, footer buttons stay reachable.
3. Dark OS theme: every surface, border, icon and focus ring comes from a token; nothing light-only (e.g. today's PromptCard copy icon has `stroke="#2B1E14"`).
4. Public address on a site (`?scenario=site`): the menu item is hidden; with `?scenario=public-unavailable` it is shown disabled with its note.
5. Deep files paths (`/p/recipe-box/files/src/...`) and a deleted/unknown project (`/p/nope`) still render inside the project layout without crashing.

---

### Task 1: Kit foundations — tokens, fonts, icons, brand mark, token invariant test

**Files:**
- Modify: `packages/ui/src/tokens.css`, `packages/ui/src/fonts.ts`, `packages/ui/package.json`, `package-lock.json`, `packages/ui/src/index.ts`, `apps/console/src/app.css`
- Create: `packages/ui/src/icons.ts`, `packages/ui/src/components/Icon.tsx`, `packages/ui/src/components/BrandMark.tsx` (+ `.module.css`)
- Test: `packages/ui/test/tokens.test.ts`

**Interfaces:**
- Produces: `type IconName` (the 48 names in `icons.json`), `Icon({ name: IconName; size?: "sm" | "md"; label?: string; className?: string })` — `<svg viewBox="0 0 24 24">`, 16px (`sm`) / 20px (`md`), `stroke: currentColor`, width 1.75, round caps/joins, `aria-hidden` unless `label` (then `role="img"` + `aria-label`); `BrandMark({ size?: number })` — the `.eg-brand-mark` CSS egg (default 18px). All exported from `@eggie/ui`.
- Produces: every design-system token from `ds-reference.css` + `colors.css` + `sizes.css` under its own name (`--page`, `--raised`, `--paper`, `--hover`, `--selected`, `--line`, `--line-strong`, `--line-input`, `--ink`…`--ink-3`, `--on-ink`, `--gold`, `--orange`, `--coral`, `--orchid`, `--periwinkle`, `--on-gold`, `--gold-ink`, `--gold-soft`, `--accent*`, `--focus`, status pairs, `--code-*`, `--scrim`, `--font-sans`, `--font-mono`, `--space-*`, `--radius-*`, `--shadow-sm|md|lg|glow`, `--control-sm|md|lg`, `--row-height`, `--icon-sm|md`, `--content-max`, `--z-*`). Skip the env/chart/semantic-alias tokens.

- [ ] **Step 1: Install deps.** `npm install` in the worktree, then `npm install -w @eggie/ui @fontsource-variable/archivo@5.3.0 @fontsource-variable/jetbrains-mono@5.3.0` and uninstall `@fontsource-variable/bricolage-grotesque`, `@fontsource-variable/hanken-grotesk`, `@fontsource/ibm-plex-mono` from `@eggie/ui`. Pin exact versions as the other deps are.

- [ ] **Step 2: Write the failing test** `packages/ui/test/tokens.test.ts`, modelled on `animations.test.ts` (paths via `import.meta.url`, skip `node_modules`/`dist`):

```ts
describe("design tokens", () => {
  it("has CSS files to check", () => { expect(files.length).toBeGreaterThan(10); });
  // A token renamed by a redesign leaves var(--old) resolving to nothing, silently.
  it("every var() names a token from tokens.css or the same file", () => {
    // defined = custom props declared in tokens.css ∪ those declared in the file itself
    // offences = `${relative path}: --name` for each var(--name) not defined
    expect(offences).toEqual([]);
  });
});
```
Scan every `.css` under `packages/ui/src` and `apps/console/src`. Then temporarily add `color: var(--nope)` to `app.css`, run `npx vitest run packages/ui/test/tokens.test.ts`, see it fail naming `--nope`, remove it.

- [ ] **Step 3: Rewrite `tokens.css`.** Design-system tokens (light on `:root, .om-theme-light`; dark in the media query and `.om-theme-dark`, including the dark shadow values). `--font-sans: "Archivo Variable", "Archivo", system-ui, sans-serif`, `--font-mono: "JetBrains Mono Variable", "JetBrains Mono", ui-monospace, …` (check the family names in the installed fontsource CSS). Keep every existing `om-*` keyframe; add `om-pulse` (`50% { opacity: .35 }`) and `om-shimmer` if a later task needs them. Keep the reduced-motion block. Add one block, commented `Old names, removed once every screen is migrated.`, aliasing the old names to new tokens: `--desk→--raised`, `--cream→--page`, `--surface→--paper`, `--surface-2→--hover`, `--line-2→--line-strong`, `--yolk→--gold`, `--yolk-deep→--gold-ink`, `--yolk-soft→--gold-soft`, `--basil→--success`, `--basil-soft→--success-soft`, `--paprika→--danger`, `--paprika-soft→--danger-soft`, `--cold→--ink-3`, `--cold-soft→--hover`, `--shadow→rgba(74,48,20,.38)` (dark `rgba(0,0,0,.65)`), `--font-display`/`--font-body→--font-sans`.

- [ ] **Step 4: Fonts.** `fonts.ts` imports `@fontsource-variable/archivo/wdth.css` (weight + width axes; confirm the file name in the package) and `@fontsource-variable/jetbrains-mono/wght.css`.

- [ ] **Step 5: `app.css` base** per `components.css`'s `html, body` rule: `background: var(--page)`, `font: 14px/20px var(--font-sans)`, `color: var(--ink)`; links `var(--accent)`, hover `var(--accent-hover)` underlined; `:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px }`; headings that use the wide display style get `font-variation-settings: "wdth" 112`.

- [ ] **Step 6: Icons.** `icons.ts` exports `ICON_PATHS` copied from `$R/design/ds/icons.json` (`as const`) and `type IconName = keyof typeof ICON_PATHS`. Implement `Icon` and `BrandMark`; export both and `IconName` from `index.ts`.

- [ ] **Step 7: Verify.** `npm test`, `npm run typecheck`, `npm run build && npm run check-offline` all pass. Start the dev server and screenshot `list|/?scenario=ok|1440|900` — the app renders in Archivo with the new palette (screens still old-shaped).

- [ ] **Step 8: Commit** `console: design-system tokens, fonts and icon set`.

---

### Task 2: Kit components follow the design system

**Files:**
- Modify: every component in `packages/ui/src/components/` (+ their `.module.css`), `packages/ui/src/index.ts`, `apps/console/src/screens/Kit.tsx` (+ `Kit.module.css`), call sites of `variant="quiet"`
- Create: `IconButton.tsx`, `Badge.tsx`, `StatusDot.tsx`, `Card.tsx`, `Choice.tsx` (+ module CSS each) in `packages/ui/src/components/`

**Interfaces:**
- Consumes: `Icon`, `IconName`, `BrandMark`, tokens (Task 1).
- Produces (all from `@eggie/ui`):
  - `Button` — `variant?: "primary" | "secondary" | "ghost" | "danger"` (default `secondary`; `quiet` is renamed `ghost` at every call site), `size?: "sm" | "md" | "lg"` (28/32/40px, default `md`), `icon?: IconName`, `iconRight?: IconName`; pills per `.eg-btn*`. An inline `<svg>` child still renders at 16px.
  - `IconButton({ icon: IconName; label: string; size?: "sm" | "md"; variant?: "ghost" | "secondary" } & ButtonHTMLAttributes)` — square pill, `aria-label={label}`, `title={label}`.
  - `Badge({ tone?: "neutral" | "success" | "warning" | "danger" | "info" | "gold" | "accent" | "outline"; icon?: IconName; children })` per `.eg-badge*`.
  - `StatusDot({ status: "running" | "starting" | "stopped" | "failed"; label?: ReactNode })` per `.eg-status`/`.eg-dot-*` (`starting` = `provisioning`: info colour, `global(om-pulse)`; `stopped` = hollow ring).
  - `StateBadge({ state: ProjectState })` keeps its API and labels and renders `<StatusDot>` (running→running, starting→starting, stopped→stopped, wrong→failed).
  - `Card({ className?, children, as?: "div" | "section" | "ul" })` — paper, 1px line, shadow-sm, 12px radius.
  - `Notice({ tone?: "info" | "warning" | "danger" | "success"; title?: ReactNode; icon?: "info" | "folder"; className?; children })` — `.eg-alert` look, 20px icon (`info`/`alert-triangle`/`x-circle`/`check-circle` by tone, `folder` when asked).
  - `Modal({ open, onClose, title, footer?: ReactNode, wide?: boolean, children })` — `.eg-dialog` (480px, `wide` 720px), head = title + `IconButton icon="x" label="Close"` calling `onClose`, scrolling body, `footer` in `.eg-dialog-foot`; backdrop `var(--scrim)`; existing open/close/cancel/backdrop behaviour unchanged.
  - `Choice({ type: "radio" | "checkbox"; label: ReactNode; description?: ReactNode } & InputHTMLAttributes)` per `.eg-check` (accent fill when checked).
  - `TextField` (same props) per `.eg-field`/`.eg-control` (32px, 12px radius, accent ring on focus, danger when invalid); the eye toggle uses `Icon`.
  - `ProgressBar` (same props): 6px `.eg-track`, accent fill; indeterminate keeps a sweeping fill.
  - `PromptCard` (same props): a `Card` with a head row (overline title, aside), the prompt in an `.eg-code` dark block, primary `Button icon="copy"`, copied state as a success line with `check-circle`.
  - `Collapsible`, `RowCard`, `SyncMarker`, `Egg` keep their props, restyled with tokens (`Egg`: paper white + gold yolk; `cold` tone uses `--ink-3`).

- [ ] **Step 1: Restyle and add the components** above. Values come from `components.css`; do not invent new ones. Hover/focus/disabled per the readme (48% opacity disabled, no press scale).
- [ ] **Step 2: Rename `variant="quiet"` → `"ghost"`** across `apps/console/src` (`grep -rn 'quiet' apps/console/src`).
- [ ] **Step 3: Kit gallery.** `Kit.tsx` (route `/kit`) shows every component and variant, incl. a dialog with footer, in light and `.om-theme-dark` side by side.
- [ ] **Step 4: Verify.** Verification set passes. Screenshot `kit|/kit|1440|900` and compare buttons, badges, dots, alerts, inputs, dialog with `components.css` / the frames.
- [ ] **Step 5: Commit** `console: kit components follow the design system`.

---

### Task 3: Shell, tabs, menus and full-page status screens

**Files:**
- Modify: `apps/console/src/shell/Shell.tsx`, `Shell.module.css`, `Tabs.tsx`, `AccountMenu.tsx`, `InstanceSwitcher.tsx`; `screens/StatusScreen.tsx` (+css), `SleepyEgg.*`, `SignedOut.tsx`, `NotAnswering.tsx`, `NeedsUpdate.tsx`, `WrongHost.tsx`, `screens/account/SignIn.*`, `screens/onboarding/Onboarding.*`

**Interfaces:**
- Consumes: `Icon`, `IconButton`, `BrandMark`, `Button`, `StatusDot`, `Notice` (Tasks 1–2); `useProjects()` from `projects/queries` for the tab count.
- Produces: `Shell({ tone?, signedIn?, nav?, trailing?, children })` — same props. Layout: page = column, full viewport height; header block on `--raised` with a bottom hairline = 48px bar row + (when `nav`) the nav row below it; `main` fills the rest. Exported CSS class names other tasks use: none — pages own their own widths.

- [ ] **Step 1: Bar row (frame 7e):** `IconButton icon="chevron-left" label="Back to Eggie"` linking to the desktop home (only when `useDesktopHome()` is set, as today — keep it an `<a href>`), `BrandMark` 18px, then the `InstanceSwitcher` trigger as a ghost pill with `chevron-down`, label in wide 650 15px; signed-out shows the word "Eggie" in the same type. `StatusDot status="running" label="Open"` when signed in. Avatar menu right: 28px gold `.eg-avatar` circle + `chevron-down`, inside a 36px pill that gets `--hover` on hover. Popover menus (`AccountMenu`, `InstanceSwitcher`) per `.eg-menu`/`.eg-menu-item` (12px radius, shadow-md, 32px items, danger item for sign-out). Bar side padding 16px (12px left).
- [ ] **Step 2: Tabs row:** `.eg-tabs` look with 16px side padding: Projects (`layers`, count badge `.eg-count` = number of projects once loaded) and Agents (`terminal`); links stay `<Link>` with `aria-current="page"`; accent 2px underline on the current one. Onboarding's `Progress` sits in the same row (restyle its dots: current = gold with `--on-gold` number, done = success).
- [ ] **Step 3: Status screens** (signed out, not answering, needs update, wrong host, sign-in): centred column, max 520px — Egg/SleepyEgg art, wide 650 title 28px, `ink-2` lede, actions row (primary + secondary). Copy unchanged.
- [ ] **Step 4: Narrow widths:** below 640px tab labels stay visible (two short tabs fit); the "Open" dot hides; menus fit `calc(100vw - 24px)`.
- [ ] **Step 5: Verify.** Verification set passes. Screenshots: `/?scenario=ok` at 1440×900 and 390×844, `/?scenario=expired`, `/?scenario=down`, `/?scenario=old-api`, `/?scenario=account-needed`, `/?scenario=kitchens` with the switcher opened (click it in a small Playwright addition), dark at 880×620.
- [ ] **Step 6: Commit** `console: shell and status screens follow the design system`.

---

### Task 4: Projects list (frame 7e) and its dialogs

**Files:**
- Create: `apps/console/src/projects/color.ts`
- Modify: `screens/list/ProjectList.tsx`, `ProjectRow.tsx`, `DiscoveredBand.tsx`, `EmptyCounter.tsx`, `NewProjectModal.tsx`, `ImportFolderModal.tsx`, `ProjectList.module.css`; `screens/github/GitHubModal.*`, `RepoPicker.tsx`

**Interfaces:**
- Produces: `projectColor(id: string): { bg: string; ink: string }` in `projects/color.ts` — `bg` is one of `var(--coral)`, `var(--gold)`, `var(--periwinkle)`, `var(--orchid)` picked by a stable string hash of `id` (e.g. sum of char codes mod 4); `ink` is `var(--on-gold)`. Task 5 uses it for the menu tile.

- [ ] **Step 1: Page head:** wide 650 30/36px "Your projects", 14px `ink-2` subtitle (today's text), actions right: `From a folder` (secondary, `folder`), `From GitHub` (secondary, `branch`), `New project` (primary, `plus`). Page content: padding 32px, centred, `max-width: var(--content-max)`. Head wraps under 640px (actions below the title, wrapping).
- [ ] **Step 2: Rows:** one `Card` holding all projects, rows separated by hairlines, min-height 72px, padding 12px 16px: 36px letter square (`projectColor`, 8px radius, 650 15px, first letter), name (600 15px, a `Link` to the page) over the line (address as mono 12px link with ellipsis / waiting text / trouble text in `--danger` / public note with `globe` in accent); a 96px column with `StateBadge`; a right-aligned actions column. Running: `Open` (secondary sm, `iconRight="external"`), `Stop` (ghost sm, `stop`), `IconButton icon="settings" label="Project settings"`. Stopped: `Start` (secondary sm, `play`) + settings icon button. Starting: elapsed text + settings icon button. Wrong/gone: `Take a look` (secondary sm, `chevron-right`). Waiting: `Open page` (secondary sm, `chevron-right`). Keep every `aria-label` that exists today. The row-level action error `Notice` stays under its row, inside the card. Under 640px the status column drops under the name and actions wrap.
- [ ] **Step 3: Footer line** under the card: `lock` icon + today's text, 13px `ink-2`. Deleted-project notices use `Notice` (success tone for "Threw out …", warning for the other).
- [ ] **Step 4: Discovered band** becomes a `Notice tone="warning"`-styled block with `folder` icon and title, its rows inside a `Card` (Adopt it = primary sm; Can't adopt = secondary sm disabled; the "What Eggie looks for" collapsible). **Empty counter**: `.eg-empty` look (egg art, 18px heading, `ink-2` text, centred actions).
- [ ] **Step 5: Dialogs** (new project, import folder, GitHub, repo picker): move each dialog's action row into `Modal`'s `footer`; radios become `Choice`; text inputs stay `TextField`; progress uses `ProgressBar`. Keep the folder-picker `cancel` handling and the files-copy-before-reset logic exactly as they are (see CLAUDE.md "Things that will bite you").
- [ ] **Step 6: Verify.** Verification set passes. Screenshots: `/?scenario=ok` 1440×900 / 880×620 / 390×844 light + 1440 dark; `/?scenario=empty`; `/?scenario=public-on`; new-project, import and GitHub dialogs opened (`?scenario=github-pending`).
- [ ] **Step 7: Commit** `console: projects list follows the design system`.

---

### Task 5: Project layout with the project menu, and the overview (frame 7f)

**Files:**
- Create: `apps/console/src/screens/project/ProjectLayout.tsx`, `ProjectMenu.tsx`, `ProjectLayout.module.css`
- Modify: `App.tsx` (routes), `screens/project/ProjectPage.tsx`, `ProjectPage.module.css`, `AddressRows.tsx`, `StartingBody.tsx`, `WrongBody.tsx`, `AnalyzeModal.tsx`, `DeleteModal.tsx`, `PublicModal.tsx`
- Delete: `screens/project/Tiles.tsx`

**Interfaces:**
- Consumes: `projectColor` (Task 4); `useProject`, `publicView`, `tileLine`, `useSite`, `filesRoute` (existing).
- Produces: route tree `<Route path="/p/:id" element={<ProjectLayout />}>` with children `index` → `ProjectPage`, `files/*` → `FilesPage`, `secrets` → `SecretsPage`. `ProjectLayout` owns the menu and the Analyze / Public / Delete dialogs and renders `<Outlet />` in a content column (padding 28px 32px, `max-width: var(--content-max)`). Child pages render only their own content (title row + body) — Task 6 relies on this.

- [ ] **Step 1: Routes and layout.** Grid `240px minmax(0,1fr)`, full height of `main`. Menu (`--raised`, right hairline, padding 20px 12px, sticky so it stays while content scrolls): `All projects` (ghost sm, `chevron-left`, link to `/`), tile row (32px letter square + name with ellipsis + `StateBadge`), items (36px, 6px radius, 16px icon, 14px; current = `--accent-soft` bg, `--accent-ink`, 600; unavailable = `--ink-3`, `not-allowed`, note right in 12px): Overview (`layers`, `NavLink end`), Analyze (`search`, button → dialog), Files (`folder`, link to `filesRoute(id, "")`, current for any `files/*`), Secrets (`key`, note `N requested` when `secrets_requested > 0`), Public address (`globe`, button → dialog; hidden when `site !== null`; disabled with note `tileLine(pub)` when `pub.kind === "unavailable"`, otherwise note `tileLine(pub)` in `ink-3`), and pinned at the bottom after a hairline: Delete project (`trash`, `--danger`). While the project is loading or not found the menu shows only All projects and the name from the URL.
- [ ] **Step 2: Overview** (`ProjectPage`): title row = wide 650 28/34px project name + 14px `ink-2` sub ("Running · N addresses open" / existing subs), actions right: running → `Restart` (ghost, `refresh`), `Stop` (secondary, `stop`), `Open in browser` (primary, `iconRight="external"`); stopped → `Start` (primary, `play`). Addresses (`AddressRows`): one `Card`, a row per address: `lock` icon (or `globe` for a public one), 110px `ink-2` label, mono 14px link with ellipsis, `Copy` (secondary sm, `copy`) — the existing `CopyButton` restyled. Notices (`restart_needed`, action errors, query errors) are `Notice` warning/danger. Remove the egg art and the tiles from the page.
- [ ] **Step 3: Other states:** starting (progress card with `ProgressBar` and the existing steps/log), stopping (sub line with elapsed), wrong (`WrongBody`: danger `Notice` with the cause, the problem detail in an `.eg-code` block, existing actions), waiting (lede + `PromptCard`), gone (title, lede, `Forget it` danger). `project_not_found` shows the title "No project called {id}". Copy unchanged.
- [ ] **Step 4: Dialogs** Analyze / Delete / Public: footer actions via `Modal footer`, danger confirm = `danger` button, checkboxes = `Choice`.
- [ ] **Step 5: Narrow widths:** under 760px the grid becomes one column; the menu turns into a horizontal row (tile + items, `overflow-x: auto`, no wrap, Delete at the end, no sticky); the All-projects link stays first.
- [ ] **Step 6: Verify.** Verification set passes. Screenshots of `/p/recipe-box?scenario=ok` at 1440×900, 880×620, 800×560, 390×844 light and 1440 dark; `/p/tiny-crm`, `/p/photo-sorter`, `/p/notes-app`, `/p/old-blog` (all `?scenario=ok`); `/p/recipe-box?scenario=site` (no Public item); `/p/recipe-box?scenario=public-unavailable`; `/p/nope?scenario=ok`. Open each of the three dialogs from the menu once.
- [ ] **Step 7: Commit** `console: project menu and overview follow the design system`.

---

### Task 6: Files and Secrets inside the project layout (frame 7i)

**Files:**
- Modify: `screens/files/FilesPage.tsx`, `Listing.tsx`, `UploadPanel.tsx`, `DoneCard.tsx`, `DestinationModal.tsx`, `FilesPage.module.css`; `screens/secrets/SecretsPage.tsx`, `SecretsPage.module.css`

**Interfaces:**
- Consumes: `ProjectLayout` renders these as children (Task 5) — remove their own back links and outer page padding/width.

- [ ] **Step 1: Files:** title row "Files" (wide 650 28/34) + sub "Everything in this project’s folder", `Upload` primary (`upload`) right. One `Card` that fills the remaining height (`flex: 1; min-height: 0`): 44px head row (`folder` icon, mono 13px path breadcrumb with today's navigation, item count right in `ink-3`), 36px `--raised` column header (Name / Size / Changed, grid `minmax(0,1fr) 120px 140px 16px`), 44px rows with hover fill, folders get a `folder` icon and a trailing `chevron-right`, names mono 13px with ellipsis, sizes tabular-nums. The body scrolls inside the card. Drop-zone, empty folder, upload panel, done card and destination dialog keep their behaviour, restyled (panel/cards = `Card`, progress = `ProgressBar`, radios = `Choice`). Under 640px the Changed column hides.
- [ ] **Step 2: Secrets:** title row "Secrets" + today's sub; each section (Requested, Your secrets, Add) a `Card` with a 15px 600 head and hairline-divided rows; values in mono; inputs `TextField`; buttons per the design (primary once: Add/Save). Copy and logic unchanged.
- [ ] **Step 3: Verify.** Verification set passes. Screenshots: `/p/recipe-box/files?scenario=ok`, a sub-folder path, `?scenario=uploads`, `?scenario=full`, `/p/recipe-box/secrets?scenario=secrets` at 1440×900, 880×620, 390×844, plus dark.
- [ ] **Step 4: Commit** `console: files and secrets follow the design system`.

---

### Task 7: Agents and onboarding (frames 7j, 7k)

**Files:**
- Modify: `screens/agents/AgentPicker.tsx`, `AgentGuide.tsx`, `AgentMenu.tsx`, `AgentStatusLine.tsx`, `MediaSlider.tsx`, `RichText.tsx` (classes only), `Agents.module.css`; `screens/onboarding/Onboarding.tsx` (+css) if its page body needs it

- [ ] **Step 1: Picker (7j):** page padding 32, centred, max `--content-max`; title "Which agent do you use?"-style head as today's copy, wide 650 30/36; grid `repeat(3, minmax(0,1fr))` gap 16 (2 columns under 900px, 1 under 560px); each agent a clickable `Card` (padding 20, hover `--line-strong` border + `--raised` bg): 40px mark square (`--raised`, inset line ring, wide 650 16px initials or today's icon), `Connected` success badge with `check-circle` when connected, `chevron-right`, name 16px 600, platform line 13px `ink-2`.
- [ ] **Step 2: Guide (7k):** `Agents` ghost sm back button (`chevron-left`), title row (wide 650 30/36) with the agent switcher (`AgentMenu`) as a secondary button with `chevron-down` on the right; grid `300px minmax(0,1fr)` gap 24: steps list (active step = paper card with shadow-sm and a gold 24px number dot; others transparent with a `--line-strong` ring dot and `ink-2` title), the connected line as a success soft block with `check-circle` (from `AgentStatusLine`), the copy-card fields; right = media `Card` (padding 20, media fills) + footer row "Step N of M" + Back (secondary) / Next (primary, `chevron-right`). Single column under 860px (steps above media).
- [ ] **Step 3: Onboarding** (`/welcome`) uses the same picker/guide; check its progress row and skip link look right in the tabs row (Task 3).
- [ ] **Step 4: Verify.** Verification set passes. Screenshots: `/agents`, `/agents/claude-code` (or the first id in `runtime/agents/index.json`), `?scenario=agents-connected`, `?scenario=agents-failed`, `/welcome?scenario=fresh` (that scenario needs `eggie.onboarded` unset — clear it in a variant run), at 1440×900, 880×620, 390×844, plus dark.
- [ ] **Step 5: Commit** `console: agents and onboarding follow the design system`.

---

### Task 8: Remove old tokens and icons, docs, final visual pass

**Files:**
- Modify: `packages/ui/src/tokens.css` (delete the old-names block), any CSS still using old names, `runtime/web/CLAUDE.md`
- Delete: `apps/console/src/screens/icons.tsx` (every icon now comes from `Icon`), unused kit pieces if nothing imports them

- [ ] **Step 1: Delete the alias block.** Run `npx vitest run packages/ui/test/tokens.test.ts`; it lists every remaining old name. Replace each with the design token it should be (not blindly the alias target — e.g. a "trouble" text is `--danger`, a card is `--paper`).
- [ ] **Step 2: Remove `screens/icons.tsx`;** replace leftover imports with `Icon` names (`PLUS→plus`, `ARROW`/`EXTERNAL→external`, `FOLDER→folder`, `MAGNIFIER→search`, `GLOBE→globe`, `TRASH→trash`, `BACK→chevron-left`, `PROJECTS→layers`, `GITHUB→branch`, `SIGN_OUT→logout`, `PLUG→terminal`, `CHEVRON_*`, `CHECK→check`, `GEAR→settings`, `KEY→key`, others by meaning). `grep -rn "#[0-9A-Fa-f]\{6\}" apps/console/src packages/ui/src --include=*.tsx --include=*.module.css` finds no colours.
- [ ] **Step 3: Docs.** `runtime/web/CLAUDE.md`: the kit is drawn from the Eggie design system (claude.ai/design project "Omelet: Linux VM Setup UI", `Eggie Host + Console v3.dc.html`, `_ds/eggie-console-design-system-…`), not `docs/design/`; icons come from `Icon` (`packages/ui/src/icons.ts`); project pages share `ProjectLayout` (menu + dialogs, children render content only); the tokens test. Add a "Things that will bite you" line if anything bit during this work.
- [ ] **Step 4: Final pass.** Verification set passes. Screenshot every route from Tasks 3–7 at 1440×900, 880×620, 800×560, 390×844, light and dark, and walk the Review Focus list. Fix what you find. No horizontal page scrollbar at any size (check `document.documentElement.scrollWidth <= innerWidth` in the screenshot script).
- [ ] **Step 5: Commit** `console: drop the old palette and icons`.
