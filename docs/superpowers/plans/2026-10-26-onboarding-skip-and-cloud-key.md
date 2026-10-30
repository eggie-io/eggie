# Onboarding Skip button + cloud key text Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move onboarding's "Skip for now" from the top bar to the bottom of the welcome screens, and tell cloud users the SSH key arrives as a zip they must unzip and point the agent at.

**Architecture:** Console-only UI move (`runtime/web/apps/console`) plus copy changes in the three `cloud` guide blocks under `runtime/agents/`. No API, no host change.

**Tech Stack:** React + TypeScript + CSS modules (Vite, Vitest); JSON agent manifests.

**Spec:** issue eggie-io/eggie-resources#114 and the design agreed in chat (summarised in Global Constraints).

## Global Constraints

- Branch `feature/114-onboarding-skip-and-key-text` from `main`.
- Top bar during onboarding shows only the progress steps and the account menu: no Skip there.
- Skip is a `variant="ghost"` `Button` labelled exactly `Skip for now`, calling `useOnboarding().finish()`.
- Guide (`/welcome/:id`): Skip sits in the bottom nav row immediately left of Back; only when `welcome`.
- Picker (`/welcome`): bottom row = muted text `Don't see yours? You can connect one any time from Agents.` followed by the Skip button. The `/agents` picker is unchanged.
- Cloud copy never tells the user to move files into `.ssh`: unzip, then use the file without `.pub` (usually in Downloads).
- "Kitchen" only in user-facing text; code names say instance/onboarding.
- No new unit tests: no React component tests exist in the console and the copy is covered by `content.test.ts` (parse + allowed tags). Verify in a headless browser instead.

## Review Focus

1. Narrow screens (≤640px): guide nav row wraps cleanly with four items (count, Skip, Back, primary); nothing overflows horizontally.
2. "Check the connection" failure message (`.checkProblem`) still takes the left slot and pushes Skip/Back/primary right.
3. `/agents/:id` (not onboarding) must not show Skip — `useOnboarding().finish()` there would write the mark and navigate.
4. Picker with zero agents or an error still shows the Skip row on `/welcome` (otherwise the user is stuck).
5. Every cloud `body`/`warning` stays inside the allowed tags (`p ul ol li strong em b i code kbd br`), or the agent silently drops.

---

### Task 1: Move Skip to the bottom of the welcome screens

**Files:**
- Modify: `runtime/web/apps/console/src/screens/onboarding/Onboarding.tsx` (replace `SkipLink` with `SkipButton`)
- Modify: `runtime/web/apps/console/src/screens/onboarding/Onboarding.module.css` (drop `.skipTail`)
- Modify: `runtime/web/apps/console/src/App.tsx:24,126` (remove import and the top-bar render)
- Modify: `runtime/web/apps/console/src/screens/agents/AgentGuide.tsx` (nav row)
- Modify: `runtime/web/apps/console/src/screens/agents/AgentPicker.tsx` (bottom row)
- Modify: `runtime/web/apps/console/src/screens/agents/Agents.module.css` (add `.skipRow`)

**Interfaces:**
- Produces: `export function SkipButton(): JSX.Element` in `Onboarding.tsx` — ghost `Button`, text `Skip for now`, `onClick={() => finish()}`.

- [ ] **Step 1:** Rename `SkipLink` → `SkipButton`, make its child plain `Skip for now` (no `skipTail` span, no `aria-label`, no `size="sm"`), delete `.skipTail` from the CSS module.
- [ ] **Step 2:** In `App.tsx` remove `{welcome && <SkipLink />}` and the `SkipLink` import.
- [ ] **Step 3:** In `AgentGuide.tsx` `Steps`, render `{welcome && <SkipButton />}` in `.nav` directly before the Back button.
- [ ] **Step 4:** In `AgentPicker.tsx` replace the welcome `<p className={s.muted}>…` with `<div className={s.skipRow}><p className={s.muted}>Don't see yours? You can connect one any time from Agents.</p><SkipButton /></div>`, rendered for every `body` state (error, loading, empty, list). Add `.skipRow { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2) var(--space-3); }` to `Agents.module.css`.
- [ ] **Step 5:** Run from `runtime/web`: `npm run typecheck` → exit 0; `npm test` → all pass.
- [ ] **Step 6:** Headless check (Playwright from `~/.npm/_npx/9833c18b2d85bc59/node_modules/playwright/index.mjs`, `executablePath: "/usr/bin/google-chrome"`, dev server `npx vite --port 5178 --strictPort` in `apps/console`, script in the session scratchpad): with `?scenario=fresh`, at 1280px and 375px widths — `/welcome` shows one `Skip for now` button below the cards and none in the header; `/welcome/claude-code` shows it in the bottom row left of Back and none in the header; clicking it lands on `/` and `localStorage["eggie.onboarded"] === "1"`; `/agents/claude-code` (after the mark) has no Skip; `document.documentElement.scrollWidth <= innerWidth` at 375px. Take screenshots of both welcome pages at both widths.
- [ ] **Step 7:** Commit: `git commit -m "Onboarding: move Skip for now to the bottom of the welcome screens"`.

### Task 2: Cloud guides — unzip the key, use the file without .pub

**Files:**
- Modify: `runtime/agents/claude-code/agent.json`, `runtime/agents/codex/agent.json`, `runtime/agents/cursor/agent.json` (`platforms.cloud` only)
- Modify: `docs/release-testing.md` row S19 (expected result; never renumber)

Exact copy, the same in all three `cloud` blocks unless noted:

- [ ] **Step 1:** Add `"warning": "<p>Unzip the <b>eggie-…_ed25519.zip</b> file you downloaded from Eggie. It holds two files: your key is the one <b>without</b> <code>.pub</code> at the end.</p>"`.
- [ ] **Step 2:** Key card row (label stays `SSH key` / `Identity file`): `"value": "The unzipped eggie-…_ed25519 file (not .pub)"`, `"hint": "Unzip the eggie-…_ed25519.zip you downloaded from Eggie. It is usually in your Downloads folder. Use the file without .pub at the end."`
- [ ] **Step 3:** Step text:
  - Claude Code, "Enter the details", second paragraph → `<p>For <b>SSH key</b>, enter the path to the unzipped key file, the one without <code>.pub</code>.</p>`
  - Codex, "Enter the details", item 2 → `<li>For the identity file, choose the unzipped key file, the one without <code>.pub</code>.</li>`
  - Cursor, "Connect to your kitchen", second paragraph → `<p>When Cursor asks for a key, choose the unzipped key file, the one without <code>.pub</code>.</p>`
- [ ] **Step 4:** `docs/release-testing.md` S19 expected column: append `; a "Before you start" note says to unzip the download and use the file without .pub`.
- [ ] **Step 5:** From `runtime/web`: `npx vitest run apps/console/src/agents` → all pass (content.test.ts parses every guide).
- [ ] **Step 6:** Headless check with `?scenario=site`: `/agents/claude-code`, `/agents/codex`, `/agents/cursor` each show the "Before you start" notice and the new key card text; hovering/focusing the ⓘ shows the new hint.
- [ ] **Step 7:** Commit: `git commit -m "Cloud agent guides: say the key arrives zipped and which file to pick"`.
