# Project initialization hand-off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every create path say who does what, show never-run projects as Waiting for agent / Needs set-up with a copy-paste hand-off card, and turn Analyze into a page.

**Architecture:** The API records each project's `origin` and whether it has ever run OK, and keeps Docker's refusal message as the project's problem. The console maps those to two new view kinds in `projects/view.ts`, builds prompts in `projects/prompts.ts`, names the connected agent through one hook, and renders a shared hand-off card and "What happens next" block.

**Tech Stack:** Python 3.12 / FastAPI / sqlite (API); React 19 + TypeScript + Vite + TanStack Query + MSW + Vitest (console).

**Spec:** `docs/superpowers/specs/2026-10-20-project-initialization-design.md` — read it; its copy and prompt texts are fixed and are not repeated here.

## Global Constraints

- Branch `feature/72-project-initialization` off `main`; worktree `../poc-72-project-init`. Do not touch `host/`; no `API_VERSION` bump (additive fields only).
- Python tests: `TMPDIR=/home/ihor/tmp python3 -m pytest -q` from the repo root (no `.venv`; `python3` is 3.12+ here).
- Console tests: `npm test` and `npm run typecheck` from `runtime/web` (run `npm install` once if `node_modules` is missing).
- No hex colours in TSX/module CSS; every `var(--x)` must exist in `packages/ui/src/tokens.css`; icons via `<Icon name>` (`packages/ui/src/icons.ts`).
- Open external addresses with `desktop.ts`'s `openExternal`, never `window.open`.
- No compat code, no backfill: no users yet.
- Comments only for non-obvious edge cases, two lines max, no ticket references.
- Tests only for logic with branches (see each task); no tests for static copy or layout.
- Agent name fallback is exactly "your coding agent" (capitalised "Your coding agent" at sentence start).

## Review Focus

1. A clone that has no `docker-compose.yml` must show Needs set-up, never the "build something" waiting card — test in Task 3 (`origin: "github", empty: true`).
2. A project that ran once and then breaks must show the red `wrong` page, not Needs set-up — test in Task 3.
3. A start that comes up but only listens on 127.0.0.1 (diagnosed) must NOT set `ran_ok` — test in Task 1.
4. Repo search with a query must keep loading pages until `has_more` is false, and stop once the query is cleared — test in Task 6 (`shouldLoadMore`).
5. A folder import with no compose file must not call `up` (it would fail with a compose error) — test in Task 7 (`hasCompose`).

---

### Task 1: API — origin, ran_ok, repo on the project payload

**Files:**
- Modify: `runtime/eggie_api/infra/migrate.py` (add `_v7_project_origin`, append to `MIGRATIONS`)
- Modify: `runtime/eggie_api/infra/repos/projects.py`
- Modify: `runtime/eggie_api/services/projects.py` (`payload`, `create`, `adopt`)
- Modify: `runtime/eggie_api/services/clone.py` (pass origin on add)
- Modify: `runtime/eggie_api/services/lifecycle.py` (mark ran ok)
- Modify: `runtime/eggie_api/rest/schemas.py`, `runtime/eggie_api/rest/routers/projects.py`
- Modify: `runtime/eggie_api/CLAUDE.md` (one line under Feature areas or Things that will bite you)
- Test: `tests/runtime/api/test_api_projects_ui.py`, `tests/runtime/api/test_migrate.py` (only if it pins `SCHEMA_VERSION`)

**Interfaces:**
- Produces (payload fields): `origin: "new" | "github" | "folder"`, `ran_ok: bool`, `repo: str | None` (`owner/name` from `infra/github.py::origin_repo(folder)` when `origin == "github"`, else `None`).
- Produces (repo): `ProjectRepo.add(id, guest_path, domain, status="stopped", origin="new")`, `ProjectRepo.mark_ran_ok(id, at: float) -> None` (sets `ran_ok_at` only if it is NULL).
- Produces (route): `POST /projects` body gains `origin: Literal["new", "folder"] = "new"`; `ProjectService.create(id, web, domain, origin="new")`.

- [ ] **Step 1: Write the failing tests** in `test_api_projects_ui.py`:

```python
def test_a_new_project_is_new_and_has_never_run(env):
    body = _create(env, "blog").json()
    assert (body["origin"], body["ran_ok"], body["repo"]) == ("new", False, None)

def test_the_console_can_mark_a_project_as_brought_from_a_folder(env):
    assert _create(env, "blog", origin="folder").json()["origin"] == "folder"

def test_create_refuses_github_as_an_origin(env):
    assert _create(env, "blog", origin="github").status_code == 422

def test_a_start_that_answers_marks_the_project_as_having_run(env):
    _create(env, "blog"); _write_compose(env, "blog")
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.client.get("/projects/blog").json()["ran_ok"] is True

def test_a_diagnosed_start_does_not_count_as_having_run(env):
    _create(env, "blog"); _write_compose(env, "blog")
    env.probe.status = 502
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.client.get("/projects/blog").json()["ran_ok"] is False

def test_a_failed_start_does_not_count_as_having_run(env):
    _create(env, "blog"); _write_compose(env, "blog")
    env.runner.up = Completed(1, "", "failed to solve: npm ci exit code 1")
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    assert env.client.get("/projects/blog").json()["ran_ok"] is False

def test_a_recheck_that_clears_a_stale_problem_marks_the_project_as_having_run(env):
    # diagnosed start (probe 502), then probe 200 and GET /projects (list rechecks)
    ...
    assert row["ran_ok"] is True
```

Also in `test_api_github.py`: extend the existing clone-then-start test (or add one) asserting the cloned project's `origin == "github"` and `repo == "octo/app"` (the fake clone must leave a `.git/config` with `[remote "origin"] url = https://github.com/octo/app.git` via `runner.on_clone`). And in `test_reconcile.py`: an adopted folder has `origin == "folder"`.

- [ ] **Step 2: Run to verify they fail** — `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_api_projects_ui.py tests/runtime/api/test_api_github.py tests/runtime/api/test_reconcile.py -q` → FAIL (`KeyError: 'origin'`).

- [ ] **Step 3: Implement.** Migration: `_add_column(conn, "projects", "origin", "TEXT NOT NULL DEFAULT 'new'")` and `_add_column(conn, "projects", "ran_ok_at", "REAL")`. In `lifecycle.start_work`, call `mark_ran_ok(project_id, time.time())` when `status == STARTED_OK and diagnosis is None`. In `payload`, call it when the recheck clears the stored problem. Clone adds with `origin="github"`, adopt with `origin="folder"`.

- [ ] **Step 4: Run the full Python suite** — `TMPDIR=/home/ihor/tmp python3 -m pytest -q` → all pass.

- [ ] **Step 5: Commit** — `git commit -m "API: project origin and ran_ok"`

### Task 2: API — keep Docker's refusal as the project's problem

**Files:**
- Modify: `runtime/eggie_api/services/lifecycle.py`
- Test: `tests/runtime/api/test_api_projects_ui.py`

**Interfaces:**
- Produces: after a start whose status is `FAILED_TO_START` with a non-empty `detail`, `problem == {"code": "failed_to_start", "message": <tail>}`; `<tail>` is the last 3 non-blank lines of `detail`, joined by `\n`, cut to 500 characters. Any later start that ends `STARTED_OK` clears it (existing `set_problem(project_id)` path).

- [ ] **Step 1: Write the failing tests**

```python
def test_a_refused_start_keeps_dockers_last_lines_as_the_problem(env):
    _create(env, "blog"); _write_compose(env, "blog")
    env.runner.up = Completed(1, "", "step 1\n\nstep 2\nstep 3\nfailed to solve: npm ci exit code 1\n")
    _run_to_completion(env, env.client.post("/projects/blog/up"))
    problem = env.client.get("/projects/blog").json()["problem"]
    assert problem == {"code": "failed_to_start",
                       "message": "step 2\nstep 3\nfailed to solve: npm ci exit code 1"}

def test_a_good_start_clears_dockers_refusal(env):
    # refused start as above, then env.runner.up/ps back to defaults and up again
    assert env.client.get("/projects/blog").json()["problem"] is None
```

- [ ] **Step 2: Run to verify they fail** → FAIL (`problem` is `None`).
- [ ] **Step 3: Implement** a module-level `_tail(detail: str) -> str` in `lifecycle.py` and set the problem in the non-`STARTED_OK` branch when `status == FAILED_TO_START and detail`.
- [ ] **Step 4: Run** `TMPDIR=/home/ihor/tmp python3 -m pytest -q` → all pass.
- [ ] **Step 5: Commit** — `git commit -m "API: keep Docker's refusal as the project's problem"`

### Task 3: Console — types, view kinds, badges

**Files:**
- Modify: `runtime/web/apps/console/src/projects/types.ts` (`Project` gains `origin: "new" | "github" | "folder"`, `ran_ok: boolean`, `repo: string | null`)
- Modify: `runtime/web/apps/console/src/projects/view.ts`
- Modify: `runtime/web/packages/ui/src/components/StateBadge.tsx`, `StatusDot.tsx` (+ its CSS)
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts` (project factory defaults: `origin: "new"`, `ran_ok: true`, `repo: null`)
- Modify: every TS place that builds a `Project` literal (typecheck finds them)
- Test: `runtime/web/apps/console/src/projects/view.test.ts`

**Interfaces:**
- Produces: `ProjectView` gains `{ kind: "setup"; badge: "setup"; detail: string | null }` and `waiting` now carries `badge: "waiting"`. `ProjectState` (kit) = `"running" | "stopped" | "starting" | "wrong" | "waiting" | "setup"`; labels "Waiting for agent" / "Needs set-up". `DotStatus` gains `"waiting"` (design's `provisioning`, accent/gold, steady) and `"degraded"` (amber, `--warning` tokens).
- Rule (order after `starting`/`stopping`/`gone`): `!ran_ok && empty && origin === "new"` → waiting; `!ran_ok && ((empty && origin !== "new") || wrong(project) !== null)` → setup with `detail = project.problem?.message ?? null`; `ran_ok && empty` → waiting (as today); else as today.

- [ ] **Step 1: Write the failing tests** (extend the `project()` factory with `origin: "new", ran_ok: true, repo: null`):

```ts
it("shows a new empty project that never ran as waiting for the agent", () => {
  expect(projectView(project({ empty: true, ran_ok: false })).kind).toBe("waiting");
});
it("shows a clone with no compose file as needing set-up, not waiting", () => {
  expect(projectView(project({ empty: true, ran_ok: false, origin: "github" })).kind).toBe("setup");
});
it("shows a never-run project whose start failed as needing set-up with Docker's message", () => {
  const view = projectView(project({ ran_ok: false, origin: "folder", status: "failed_to_start", problem: problem("failed_to_start") }));
  expect(view).toEqual({ kind: "setup", badge: "setup", detail: "failed_to_start message" });
});
it("keeps a failure after the first good run as wrong", () => {
  expect(projectView(project({ ran_ok: true, origin: "github", status: "failed_to_start" })).kind).toBe("wrong");
});
it("leaves a never-run project that is just stopped as stopped", () => {
  expect(projectView(project({ ran_ok: false, origin: "github" })).kind).toBe("stopped");
});
it("still shows a starting job over set-up", () => {
  expect(projectView(project({ ran_ok: false, origin: "github", empty: true, job: job("clone") })).kind).toBe("starting");
});
```

- [ ] **Step 2: Run** `npx vitest run apps/console/src/projects/view.test.ts` → FAIL.
- [ ] **Step 3: Implement** the rule in `view.ts` and the kit states. Existing switch statements on `view.kind` (`ProjectRow`, `ProjectPage`) get a temporary `setup` case reusing the `wrong` rendering; Tasks 4–5 replace it.
- [ ] **Step 4: Run** `npm test && npm run typecheck` in `runtime/web` → pass.
- [ ] **Step 5: Commit** — `git commit -m "Console: waiting and needs set-up project states"`

### Task 4: Console — agent name, prompts, hand-off card on the project page

**Files:**
- Create: `runtime/web/apps/console/src/agents/current.ts` (+ `current.test.ts`)
- Modify: `runtime/web/apps/console/src/agents/queries.ts` (add `useCurrentAgent`)
- Modify: `runtime/web/apps/console/src/projects/prompts.ts` (+ create `prompts.test.ts`)
- Create: `runtime/web/apps/console/src/screens/project/HandoffCard.tsx` (+ styles in `ProjectPage.module.css`)
- Modify: `runtime/web/apps/console/src/screens/project/ProjectPage.tsx`
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts` (scenarios `handoff-new`, `handoff-github`, `handoff-folder`)

**Interfaces:**
- Produces: `currentAgent(agents: Agent[] | undefined, statuses: AgentStatuses | undefined): { name: string; connected: boolean; id: string | null }` — first catalog agent with `connected: true` → `{ name, connected: true, id }`; none → `{ name: "your coding agent", connected: false, id: null }`. `useCurrentAgent()` combines `useAgents()` and `useAgentStatus()`.
- Produces: `newPrompt(id: string): string`, `setupPrompt(id: string, origin: Project["origin"], detail: string | null): string`, `analyzePrompt(focus: Focus, id: string, detail: string | null): string`, `type Focus = "full" | "start" | "share"`, `ANALYZE_GETS: Record<Focus, string[]>`. Texts are the spec's. `setupPrompt` says "from GitHub" / "from a folder" / nothing for `new`, and appends `\n\nDocker said: <detail>` when `detail` is set. `analyzePrompt("start", …)` follows the spec's no-problem rule. `waitingPrompt` and `ANALYZE_PROMPT` go.
- Produces: `HandoffCard({ title, agent, steps })` where `title` is the header text, `agent` is the `currentAgent` result, and `steps` is `{ prompt: string; extra?: string; copyHint: string; pasteHint: string; last: ReactNode }`. It renders the spec's three numbered rows. The header shows Badge "Connected" (success, `check-circle`) or "Not connected", plus a ghost "Switch agent" `Link` to `/agents`. Task 8 reuses it with `last` = the "What you'll get back" chips.

- [ ] **Step 1: Write the failing tests**: `current.test.ts` covers first-connected-in-catalog-order, none connected → fallback, statuses undefined → fallback. `prompts.test.ts` covers setup wording per origin, the Docker line present/absent, the `start` analyze prompt with and without detail, and that `newPrompt` contains `~/projects/<id>` and `eggie up`.
- [ ] **Step 2: Run** `npx vitest run apps/console/src/agents/current.test.ts apps/console/src/projects/prompts.test.ts` → FAIL.
- [ ] **Step 3: Implement**, then render: the `waiting` body (spec 8d lead line + card) and the `setup` body (8e lead; for github, `repo` linked to `https://github.com/<repo>` via `openExternal`; card with **Try now**, which calls `lifecycle.mutate("up")`, disabled while pending, errors through `actionError`). Copy uses the agent name, with "Your coding agent" at a sentence start. Title row badge comes from the view.
- [ ] **Step 4: Run** `npm test && npm run typecheck` → pass. Then check `?scenario=handoff-new`, `handoff-github` and `handoff-folder` headlessly (see the memory note on Playwright + `npx vite --port 5178 --strictPort`), at 1112px and 390px wide.
- [ ] **Step 5: Commit** — `git commit -m "Console: hand-off card for new and set-up projects"`

### Task 5: Console — projects list rows and subtitle

**Files:**
- Modify: `runtime/web/apps/console/src/screens/list/ProjectRow.tsx`, `ProjectList.tsx`, `ProjectList.module.css`
- Modify: `runtime/web/apps/console/src/projects/format.ts` (+ its test file)

**Interfaces:**
- Consumes: `useCurrentAgent()` (Task 4), view kinds (Task 3).
- Produces: `subtitle(total: number, cooking: number, waiting = 0, agent = "your coding agent"): string`. With `waiting > 0` → `"<N> on the go · <m> waiting on <agent>"` (m lower-cased like `cooking`); otherwise the existing output.
- Rows: `waiting` → note "Empty folder · ‹Agent› builds it from one prompt", primary sm **Next step** (chevron-right). `setup` → note "From GitHub · …" / "From a folder · …" / "‹Agent› makes it run here", secondary sm **Set it up**. Both navigate to the page; the note carries a small agent mark (letter tile, as in the design).

- [ ] **Step 1: Failing test** in format's test: `subtitle(6, 3, 2, "Claude Code") === "Six on the go · two waiting on Claude Code"`; `subtitle(6, 3, 0, "Claude Code")` equals the old cooking output.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** `npm test && npm run typecheck`; check `?scenario=handoff-github` list headlessly.
- [ ] **Step 5: Commit** — `git commit -m "Console: list rows for projects waiting on the agent"`

### Task 6: Console — "What happens next" block, New project dialog, GitHub dialog with search

**Files:**
- Create: `runtime/web/apps/console/src/screens/list/WhatNext.tsx` (+ CSS module)
- Modify: `runtime/web/apps/console/src/screens/list/NewProjectModal.tsx`
- Modify: `runtime/web/apps/console/src/screens/github/GitHubModal.tsx`, `RepoPicker.tsx`, `GitHubModal.module.css`
- Modify: `runtime/web/apps/console/src/github/view.ts` (+ `view.test.ts`)

**Interfaces:**
- Produces: `WhatNext({ agent: string, first: string, middle: string })` renders the three rows (Eggie egg, agent mark with highlighted `--gold-soft` row, Eggie egg). The last row's text is fixed: "‹Agent› starts it with `eggie up` and gives you an address". Task 7 reuses it.
- Produces: `filterRepos(repos: Repo[], query: string): Repo[]` (trimmed, case-insensitive substring of `full_name`; empty query → all) and `shouldLoadMore(query: string, hasNextPage: boolean, isFetching: boolean): boolean` (true only when the trimmed query is non-empty, there is a next page, and nothing is fetching).
- New project: title "New project", hint `Folder: ~/projects/<slug>`, buttons Cancel / **Make the folder** (`iconRight="chevron-right"`), WhatNext texts from spec 8a.
- GitHub: title "Bring in from GitHub". In the ready state a search `TextField` (label "Search", icon `search`) sits above the list. `RepoPicker` keeps a `selected: string | null`, and a row click selects it (`aria-pressed`). WhatNext (spec 8b) shows under the list once something is selected. The footer has Cancel and **Bring it in** (`icon="download"`, disabled with no selection), and Bring it in calls the existing `start(repo)`. The footer lives in `GitHubModal`, so lift `selected` and the start trigger there the way `started` already is. Name-taken renaming and "Already a project → Open" keep their current behaviour. An effect calls `fetchNextPage()` while `shouldLoadMore(...)` is true.

- [ ] **Step 1: Failing tests** in `github/view.test.ts` for `filterRepos` (case, trim, empty) and `shouldLoadMore` (query + more → true; empty query → false; fetching → false; no next page → false).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** `npm test && npm run typecheck`; check the New project dialog and `?scenario=ok` GitHub dialog (search, select, Bring it in) headlessly.
- [ ] **Step 5: Commit** — `git commit -m "Console: what-happens-next in New project and GitHub dialogs, repo search"`

### Task 7: Console — From a folder: origin, start after upload, What happens next

**Files:**
- Modify: `runtime/web/apps/console/src/imports/importApi.ts` (create sends `{ id, origin: "folder" }`; add `up(id)` → `POST /api/projects/{id}/up`)
- Modify: `runtime/web/apps/console/src/imports/send.ts` (`ImportApi` gains `up`)
- Modify: `runtime/web/apps/console/src/imports/plan.ts` (+ test): `hasCompose(plan: ImportPlan): boolean`
- Modify: `runtime/web/apps/console/src/screens/list/ImportFolderModal.tsx`
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts` (POST /api/projects stores `origin`)

**Interfaces:**
- Consumes: `WhatNext` (Task 6).
- Produces: `hasCompose(plan)` is true when an entry's path is exactly `docker-compose.yml` (the API's `constants.COMPOSE_FILE`).
- After `runImport` succeeds and `hasCompose(plan)`: call `importApi.up(plan.id)`. Ignore `project_busy`; any other error still navigates, and the page shows the state. Then navigate as today. WhatNext in the pick step: first "Copies the folder into Eggie", middle as in 8b.

- [ ] **Step 1: Failing test** in `plan.test.ts`: `hasCompose` is true for a top-level `docker-compose.yml`, false for `app/docker-compose.yml` and for `compose.yaml`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** `npm test && npm run typecheck`.
- [ ] **Step 5: Commit** — `git commit -m "Console: folder import starts the project and says what comes next"`

### Task 8: Console — Analyze page

**Files:**
- Create: `runtime/web/packages/ui/src/components/SegmentedControl.tsx` (+ `.module.css`, export from `index.ts`, add to `/kit` gallery in `apps/console/src/screens/Kit.tsx`)
- Create: `runtime/web/apps/console/src/screens/project/AnalyzePage.tsx`
- Delete: `runtime/web/apps/console/src/screens/project/AnalyzeModal.tsx`
- Modify: `App.tsx` (route `analyze` under `/p/:id`), `ProjectMenu.tsx` (Analyze becomes a `NavLink` to `${page}/analyze`; `ProjectDialog` drops `"analyze"`), `ProjectLayout.tsx`

**Interfaces:**
- Consumes: `analyzePrompt`, `ANALYZE_GETS`, `Focus` (Task 4), `HandoffCard`, `useCurrentAgent`, `projectView`.
- Produces: `SegmentedControl<T extends string>({ label, options: { value: T; label: string }[], value: T, onChange(v: T) })`. It is a `role="radiogroup"` of buttons with `aria-checked`, and arrow keys move the selection.
- The page uses the spec 8f copy. The default focus is `"start"` when the view kind is `wrong` or `setup`, else `"full"`. The `start` prompt gets `detail` from the view (`setup.detail`, or `wrong.detail`/problem message).

- [ ] **Step 1:** No new unit test (the prompt logic is covered in Task 4; the control is layout). Run `npm test && npm run typecheck` after implementing.
- [ ] **Step 2: Implement**, then check `/p/<id>/analyze` headlessly in both themes, switching all three focuses.
- [ ] **Step 3: Commit** — `git commit -m "Console: Analyze is a page with three questions"`

### Task 9: Docs

**Files:**
- Modify: `runtime/web/CLAUDE.md` (Structure: view kinds `waiting`/`setup`, `agents/current.ts`, Analyze page, new scenarios). Edit existing lines and keep it short.
- Modify: `docs/release-testing.md`. Append **B6** to "Both platforms, once set up": "Clone a repo with no docker-compose.yml, then a folder whose compose fails" | "Both show Needs set-up with a prompt (the second with Docker's message); after the agent runs `eggie up` successfully, a later failure shows Something's wrong". Never renumber existing cases.

- [ ] **Step 1: Edit** both files. **Step 2:** `TMPDIR=/home/ihor/tmp python3 -m pytest -q` and `npm test` → pass. **Step 3: Commit** — `git commit -m "Docs: project hand-off states"`
