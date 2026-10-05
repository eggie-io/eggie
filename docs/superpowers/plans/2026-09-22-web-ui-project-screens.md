# Web UI Project Screens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the signed-in placeholder with the real project screens — list, discovered band, new project, project page in every state, Analyze, delete — against the agent's existing `/api` routes, with a stateful mock agent for `npm run dev`.

**Architecture:** Every decision lives in small pure modules under `apps/console/src/projects/` (`view.ts`, `format.ts`, `slugify.ts`, `copy.ts`, `prompts.ts`) and is unit-tested; `queries.ts` wraps the agent routes in TanStack Query hooks; screens under `apps/console/src/screens/list/` and `screens/project/` are thin and untested (the human walks them). The kit gains `TextField`, a PromptCard clipboard-refused message and `Collapsible.onToggle`.

**Tech Stack:** React 19.3, TypeScript 7.0.2, Vite 8.3, react-router 8.4 (imports from `"react-router"`), @tanstack/react-query 5.103, MSW 2.15, Vitest 5.0 (node environment, no DOM). Python 3.12 + pytest for one fixture test.

**Spec:** `docs/superpowers/specs/2026-09-22-web-ui-project-screens-design.md`

## Global Constraints

- Work in `web/`; run `npm test`, `npm run typecheck`, `npm run build && npm run check-offline` from `web/`. Python tests: `TMPDIR=$PWD/.superpowers/tmp python3 -m pytest -q` from the repo root.
- No new npm dependencies. No component-rendering tests, no snapshot tests. Tests only for logic with branches; every test name states the guarantee.
- `packages/ui` may import only React, its fonts and its own files (`packages/ui/test/boundary.test.ts`). Nothing in `packages/ui` knows about projects or the API.
- Nothing from `host/desktop/ui` is reused.
- Copy in quotes in the spec is final; board copy is used verbatim.
- Sync marker stays hidden. Files tile disabled with "Soon"; Public address tile disabled with "Needs an account".
- Comments only for edge cases or workarounds; never mention tickets, docs or tasks.
- Styles: CSS Modules using the kit's tokens (`--ink`, `--ink-2`, `--ink-3`, `--surface`, `--surface-2`, `--line`, `--line-2`, `--yolk`, `--yolk-soft`, `--yolk-deep`, `--basil`, `--basil-soft`, `--paprika`, `--paprika-soft`, `--cold`, `--cold-soft`, `--cream`, `--shadow`, `--font-display`, `--font-body`, `--font-mono`). Layout breakpoint is `@media (min-width: 1040px)`.
- Commit after each task, message in plain imperative English, ending with the attribution lines the controller gives you.

---

## File Structure

```
web/apps/console/src/
  projects/
    types.ts          agent payload types
    view.ts           projectView(): kind + badge + cause         (tested)
    format.ts         elapsed, subtitle, sizes, relative time, delete rows (tested)
    slugify.ts        the agent's _slug rule                       (tested against shared fixture)
    copy.ts           phase captions, cause copy, action errors
    prompts.ts        Analyze / fix / waiting prompts
    queries.ts        TanStack hooks over /api
    useNow.ts         ticking clock hook
  components/Elapsed.tsx
  screens/list/       ProjectList, ProjectRow, DiscoveredBand, NewProjectModal, EmptyCounter (+ .module.css)
  screens/project/    ProjectPage, StartingBody, WrongBody, AddressRows, Tiles, AnalyzeModal, DeleteModal (+ .module.css)
  screens/WrongHost.tsx
  boot/boot.ts        + wrongHost
  api/client.ts       + text()
  mocks/handlers.ts   stateful fake agent
web/packages/ui/src/components/
  TextField.tsx (+ .module.css), PromptCard.tsx (refused copy), Collapsible.tsx (onToggle)
tests/fixtures/slugify-cases.json
tests/agent/test_slug_cases.py
```

Removed: `screens/Projects.tsx`, `screens/Projects.module.css`.

---

### Task 1: Project types and `projectView`

**Files:**
- Create: `web/apps/console/src/projects/types.ts`
- Create: `web/apps/console/src/projects/view.ts`
- Test: `web/apps/console/src/projects/view.test.ts`

**Interfaces:**
- Produces: the types below; `projectView(project: Project): ProjectView`; `type Cause`.

- [ ] **Step 1: Write the types**

`web/apps/console/src/projects/types.ts`:

```ts
export type Status = "stopped" | "started_ok" | "failed_to_start" | "crash_looping";

export interface Problem {
  code: string;
  message: string;
}

export interface WebEntry {
  url: string;
  service: string;
  primary: boolean;
}

export type JobKind = "up" | "restart" | "down";

export interface ActiveJob {
  id: string;
  kind: JobKind;
  phase: string;
  started_at: number;
}

export interface Project {
  id: string;
  status: Status;
  domain: string;
  path: string;
  urls: string[];
  problem: Problem | null;
  empty: boolean;
  web: WebEntry[];
  first_run: boolean;
  job: ActiveJob | null;
}

export interface Discovered {
  name: string;
  seen_at: number;
  adoptable: boolean;
  reason: "bad_name" | "compose_missing" | null;
}

export interface ProjectList {
  projects: Project[];
  discovered: Discovered[];
}

export interface Job {
  job_id: string;
  kind: JobKind;
  phase: string;
  state: "running" | "done" | "failed";
  detail: string;
  result: unknown;
  started_at: number;
  finished_at: number | null;
}

export interface DeletePreview {
  files: number;
  bytes: number;
  containers: string[];
  volumes: string[];
}

export interface DeleteResult {
  id: string;
  stopped: boolean;
  detail: string;
}
```

- [ ] **Step 2: Write the failing test**

`web/apps/console/src/projects/view.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { ActiveJob, Project } from "./types";
import { projectView } from "./view";

function project(over: Partial<Project> = {}): Project {
  return {
    id: "recipe-box",
    status: "stopped",
    domain: "127-0-0-1.sslip.io",
    path: "/opt/eggie/projects/recipe-box",
    urls: [],
    problem: null,
    empty: false,
    web: [],
    first_run: false,
    job: null,
    ...over,
  };
}

const job = (kind: ActiveJob["kind"]): ActiveJob => ({ id: "j1", kind, phase: "preparing", started_at: 1 });
const problem = (code: string) => ({ code, message: `${code} message` });

describe("projectView", () => {
  it("shows a running up or restart job as starting, even over a stale problem", () => {
    for (const kind of ["up", "restart"] as const) {
      const view = projectView(project({ job: job(kind), problem: problem("bound_to_loopback") }));
      expect([view.kind, view.badge]).toEqual(["starting", "starting"]);
    }
  });

  it("shows a running down job as stopping with the stopped badge", () => {
    const view = projectView(project({ status: "started_ok", job: job("down") }));
    expect([view.kind, view.badge]).toEqual(["stopping", "stopped"]);
  });

  it("calls a project whose folder is gone 'gone', not a generic fault", () => {
    const view = projectView(project({ problem: problem("folder_missing") }));
    expect([view.kind, view.badge]).toEqual(["gone", "wrong"]);
  });

  it("treats an empty folder as waiting even though the agent reports compose_missing", () => {
    const view = projectView(project({ empty: true, problem: problem("compose_missing") }));
    expect([view.kind, view.badge]).toEqual(["waiting", "stopped"]);
  });

  it("names the cause from the problem code before the status", () => {
    const view = projectView(project({ status: "failed_to_start", problem: problem("bound_to_loopback") }));
    expect(view).toEqual({ kind: "wrong", badge: "wrong", cause: "bound_to_loopback", detail: null });
  });

  it("falls back to the status when there is no problem", () => {
    for (const status of ["failed_to_start", "crash_looping"] as const) {
      expect(projectView(project({ status }))).toEqual({ kind: "wrong", badge: "wrong", cause: status, detail: null });
    }
  });

  it("groups files Eggie can't read under 'unreadable' and keeps the agent's message", () => {
    for (const code of ["invalid_compose", "invalid_project", "compose_missing"]) {
      expect(projectView(project({ problem: problem(code) }))).toEqual({
        kind: "wrong",
        badge: "wrong",
        cause: "unreadable",
        detail: `${code} message`,
      });
    }
  });

  it("shows an unknown problem code as unreachable with the agent's message", () => {
    expect(projectView(project({ status: "started_ok", problem: problem("brand_new_code") }))).toEqual({
      kind: "wrong",
      badge: "wrong",
      cause: "service_unreachable",
      detail: "brand_new_code message",
    });
  });

  it("is running only when started_ok with no problem", () => {
    const view = projectView(project({ status: "started_ok" }));
    expect([view.kind, view.badge]).toEqual(["running", "running"]);
  });

  it("is stopped otherwise", () => {
    const view = projectView(project());
    expect([view.kind, view.badge]).toEqual(["stopped", "stopped"]);
  });
});
```

- [ ] **Step 3: Run it to see it fail**

Run (from `web/`): `npx vitest run apps/console/src/projects/view.test.ts`
Expected: FAIL — cannot find module `./view`.

- [ ] **Step 4: Implement**

`web/apps/console/src/projects/view.ts`:

```ts
import type { ProjectState } from "@eggie/ui";
import type { ActiveJob, Project } from "./types";

export type Cause = "bound_to_loopback" | "service_unreachable" | "crash_looping" | "failed_to_start" | "unreadable";

export type ProjectView =
  | { kind: "starting"; badge: ProjectState; job: ActiveJob }
  | { kind: "stopping"; badge: ProjectState; job: ActiveJob }
  | { kind: "gone" | "waiting" | "running" | "stopped"; badge: ProjectState }
  | { kind: "wrong"; badge: "wrong"; cause: Cause; detail: string | null };

const NAMED: Record<string, Cause> = {
  bound_to_loopback: "bound_to_loopback",
  service_unreachable: "service_unreachable",
  crash_looping: "crash_looping",
  failed_to_start: "failed_to_start",
};

const UNREADABLE = new Set(["invalid_compose", "invalid_project", "compose_missing"]);

function wrong(project: Project): ProjectView | null {
  const code = project.problem?.code ?? (project.status === "failed_to_start" || project.status === "crash_looping" ? project.status : null);
  if (code === null) return null;
  const message = project.problem?.message ?? null;
  if (UNREADABLE.has(code)) return { kind: "wrong", badge: "wrong", cause: "unreadable", detail: message };
  if (Object.hasOwn(NAMED, code)) return { kind: "wrong", badge: "wrong", cause: NAMED[code], detail: null };
  return { kind: "wrong", badge: "wrong", cause: "service_unreachable", detail: message };
}

export function projectView(project: Project): ProjectView {
  const job = project.job;
  if (job && (job.kind === "up" || job.kind === "restart")) return { kind: "starting", badge: "starting", job };
  if (job && job.kind === "down") return { kind: "stopping", badge: "stopped", job };
  if (project.problem?.code === "folder_missing") return { kind: "gone", badge: "wrong" };
  if (project.empty) return { kind: "waiting", badge: "stopped" };
  const fault = wrong(project);
  if (fault) return fault;
  if (project.status === "started_ok") return { kind: "running", badge: "running" };
  return { kind: "stopped", badge: "stopped" };
}
```

- [ ] **Step 5: Run the tests and typecheck**

Run: `npx vitest run apps/console/src/projects/view.test.ts && npm run typecheck`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add web/apps/console/src/projects/types.ts web/apps/console/src/projects/view.ts web/apps/console/src/projects/view.test.ts
git commit -m "Derive a project's screen state from the agent's payload"
```

---

### Task 2: Formatting and the shared slug rule

**Files:**
- Create: `web/apps/console/src/projects/format.ts`
- Create: `web/apps/console/src/projects/slugify.ts`
- Create: `tests/fixtures/slugify-cases.json`
- Create: `tests/agent/test_slug_cases.py`
- Test: `web/apps/console/src/projects/format.test.ts`, `web/apps/console/src/projects/slugify.test.ts`

**Interfaces:**
- Consumes: `DeletePreview` from `./types`.
- Produces: `elapsed(startedAtSec: number, nowMs: number): string`, `subtitle(total: number, cooking: number): string`, `folderHeading(count: number): string`, `size(bytes: number): string`, `relativeTime(atSec: number, nowMs: number): string`, `hostOf(url: string): string`, `interface BinRow { label: string; detail: string; names?: string[] }`, `deleteRows(preview: DeletePreview): BinRow[]`, `slugify(name: string): string`.

- [ ] **Step 1: Write the shared fixture and the agent-side test**

`tests/fixtures/slugify-cases.json`:

```json
[
  {"name": "weekend-shop", "slug": "weekend-shop"},
  {"name": "Weekend Shop", "slug": "weekend-shop"},
  {"name": "  My App  ", "slug": "my-app"},
  {"name": "tax_stuff 2024", "slug": "tax-stuff-2024"},
  {"name": "--hello--", "slug": "hello"},
  {"name": "a  --  b", "slug": "a----b"},
  {"name": "Émile's Café", "slug": "mile-s-caf"},
  {"name": "!!!", "slug": ""}
]
```

`tests/agent/test_slug_cases.py`:

```python
"""The web page previews a new project's id before the agent creates it; both
sides read these cases so the preview can't promise an id the agent won't make."""
import json
from pathlib import Path

from agent.core.project import _slug

CASES = Path(__file__).resolve().parents[1] / "fixtures" / "slugify-cases.json"


def test_the_agent_slugs_every_shared_case_the_way_the_page_previews_it():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert cases, "no shared slug cases -- this test is no longer guarding anything"
    for case in cases:
        assert _slug(case["name"]) == case["slug"], case
```

Run (repo root): `TMPDIR=$PWD/.superpowers/tmp python3 -m pytest tests/agent/test_slug_cases.py -q`
Expected: PASS (the fixture describes the agent's existing rule).

- [ ] **Step 2: Write the failing web tests**

`web/apps/console/src/projects/slugify.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { slugify } from "./slugify";

const CASES: { name: string; slug: string }[] = JSON.parse(
  readFileSync(new URL("../../../../../tests/fixtures/slugify-cases.json", import.meta.url), "utf-8"),
);

describe("slugify", () => {
  it("has shared cases to check", () => {
    expect(CASES.length).toBeGreaterThan(0);
  });

  it("previews the same id the agent creates for every shared case", () => {
    for (const { name, slug } of CASES) expect(slugify(name), name).toBe(slug);
  });
});
```

`web/apps/console/src/projects/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { deleteRows, elapsed, folderHeading, relativeTime, size, subtitle } from "./format";

describe("elapsed", () => {
  it("never shows negative time when the VM clock runs ahead", () => {
    expect(elapsed(1_000, 999_000)).toBe("0:00");
  });

  it("shows minutes and padded seconds under an hour", () => {
    expect(elapsed(0, 72_000)).toBe("1:12");
    expect(elapsed(0, 3_599_000)).toBe("59:59");
  });

  it("adds hours from sixty minutes", () => {
    expect(elapsed(0, 3_600_000)).toBe("1:00:00");
    expect(elapsed(0, 3_723_000)).toBe("1:02:03");
  });
});

describe("subtitle", () => {
  it("spells counts up to nine and uses digits after", () => {
    expect(subtitle(4, 2)).toBe("Four on the go · two of them cooking");
    expect(subtitle(9, 1)).toBe("Nine on the go · one of them cooking");
    expect(subtitle(10, 10)).toBe("10 on the go · 10 of them cooking");
  });

  it("leaves out the cooking clause when nothing runs", () => {
    expect(subtitle(3, 0)).toBe("Three on the go");
  });

  it("has its own line for no projects", () => {
    expect(subtitle(0, 0)).toBe("Nothing on the go yet");
  });
});

describe("folderHeading", () => {
  it("is singular for one folder and counted otherwise", () => {
    expect(folderHeading(1)).toBe("A folder turned up");
    expect(folderHeading(2)).toBe("Two folders turned up");
    expect(folderHeading(12)).toBe("12 folders turned up");
  });
});

describe("size", () => {
  it("uses the largest whole unit with one decimal below ten", () => {
    expect(size(0)).toBe("0 B");
    expect(size(1023)).toBe("1023 B");
    expect(size(1024)).toBe("1 KB");
    expect(size(1536)).toBe("1.5 KB");
    expect(size(38 * 1024 * 1024)).toBe("38 MB");
    expect(size(5 * 1024 ** 3)).toBe("5 GB");
  });
});

describe("relativeTime", () => {
  it("rounds down to the largest unit", () => {
    expect(relativeTime(1_000, 1_030_000)).toBe("just now");
    expect(relativeTime(1_000, 1_060_000)).toBe("1 minute ago");
    expect(relativeTime(1_000, 1_240_000)).toBe("4 minutes ago");
    expect(relativeTime(0, 7_200_000)).toBe("2 hours ago");
    expect(relativeTime(0, 86_400_000)).toBe("1 day ago");
  });

  it("says just now for a time slightly in the future", () => {
    expect(relativeTime(2_000, 1_000_000)).toBe("just now");
  });
});

describe("deleteRows", () => {
  it("lists files, machines and stored data with real names", () => {
    expect(
      deleteRows({ files: 412, bytes: 38 * 1024 * 1024, containers: ["shop-web-1"], volumes: ["shop_db"] }),
    ).toEqual([
      { label: "Every file in the project", detail: "412 files · 38 MB" },
      { label: "The little machines that run it", detail: "stopped & removed", names: ["shop-web-1"] },
      { label: "Its stored data", detail: "deleted", names: ["shop_db"] },
    ]);
  });

  it("leaves out anything there is none of", () => {
    expect(deleteRows({ files: 1, bytes: 10, containers: [], volumes: [] })).toEqual([
      { label: "Every file in the project", detail: "1 file · 10 B" },
    ]);
    expect(deleteRows({ files: 0, bytes: 0, containers: [], volumes: [] })).toEqual([]);
  });
});
```

- [ ] **Step 3: Run them to see them fail**

Run (from `web/`): `npx vitest run apps/console/src/projects`
Expected: FAIL — cannot find modules `./slugify`, `./format`.

- [ ] **Step 4: Implement**

`web/apps/console/src/projects/slugify.ts`:

```ts
// Must match the agent's _slug: runs of anything outside [a-z0-9-] become one
// dash, but existing dashes are kept as they are.
export function slugify(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
}
```

`web/apps/console/src/projects/format.ts`:

```ts
import type { DeletePreview } from "./types";

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"];

function counted(n: number): string {
  return n < WORDS.length ? WORDS[n] : String(n);
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function elapsed(startedAtSec: number, nowMs: number): string {
  const total = Math.max(0, Math.floor(nowMs / 1000 - startedAtSec));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

export function subtitle(total: number, cooking: number): string {
  if (total === 0) return "Nothing on the go yet";
  const head = `${counted(total)} on the go`;
  return cooking === 0 ? head : `${head} · ${counted(cooking).toLowerCase()} of them cooking`;
}

export function folderHeading(count: number): string {
  return count === 1 ? "A folder turned up" : `${counted(count)} folders turned up`;
}

const UNITS = ["B", "KB", "MB", "GB", "TB"];

export function size(bytes: number): string {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const shown = unit > 0 && value < 10 ? value.toFixed(1).replace(/\.0$/, "") : String(Math.round(value));
  return `${shown} ${UNITS[unit]}`;
}

export function relativeTime(atSec: number, nowMs: number): string {
  const seconds = Math.max(0, nowMs / 1000 - atSec);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${plural(Math.floor(seconds / 60), "minute")} ago`;
  if (seconds < 86_400) return `${plural(Math.floor(seconds / 3600), "hour")} ago`;
  return `${plural(Math.floor(seconds / 86_400), "day")} ago`;
}

export function hostOf(url: string): string {
  return url.replace(/^https?:\/\//, "");
}

export interface BinRow {
  label: string;
  detail: string;
  names?: string[];
}

export function deleteRows(preview: DeletePreview): BinRow[] {
  const rows: BinRow[] = [];
  if (preview.files > 0) {
    rows.push({ label: "Every file in the project", detail: `${plural(preview.files, "file")} · ${size(preview.bytes)}` });
  }
  if (preview.containers.length > 0) {
    rows.push({ label: "The little machines that run it", detail: "stopped & removed", names: preview.containers });
  }
  if (preview.volumes.length > 0) {
    rows.push({ label: "Its stored data", detail: "deleted", names: preview.volumes });
  }
  return rows;
}
```

- [ ] **Step 5: Run everything**

Run (from `web/`): `npm test && npm run typecheck`; (repo root) `TMPDIR=$PWD/.superpowers/tmp python3 -m pytest tests/agent/test_slug_cases.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/slugify-cases.json tests/agent/test_slug_cases.py web/apps/console/src/projects/format.ts web/apps/console/src/projects/format.test.ts web/apps/console/src/projects/slugify.ts web/apps/console/src/projects/slugify.test.ts
git commit -m "Format project times and sizes, and preview ids with the agent's slug rule"
```

---

### Task 3: Wrong-host boot result and screen

**Files:**
- Modify: `web/apps/console/src/boot/boot.ts`
- Modify: `web/apps/console/src/boot/boot.test.ts` (the test "is notAnswering for any other refusal of the session check")
- Create: `web/apps/console/src/screens/WrongHost.tsx`
- Modify: `web/apps/console/src/App.tsx`

**Interfaces:**
- Produces: `BootResult` gains `{ kind: "wrongHost" }`.

- [ ] **Step 1: Change the tests**

In `boot.test.ts`, replace the test `"is notAnswering for any other refusal of the session check"` (it uses a 403 `forbidden_host`, which now means something else) with:

```ts
  it("is notAnswering for any other refusal of the session check", async () => {
    const { fetch } = agent({ "GET /api/health": HEALTHY, "GET /api/session": refusal(500, "internal_error") });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "notAnswering" });
  });

  it("is wrongHost when the agent refuses the page's address", async () => {
    const { fetch } = agent({ "GET /api/health": refusal(403, "forbidden_host") });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "wrongHost" });
  });

  it("is wrongHost when the agent refuses the page's origin on sign-in", async () => {
    const { fetch } = agent({ "GET /api/health": HEALTHY, "POST /api/session": refusal(403, "forbidden_origin") });
    expect(await boot({ fetch, handoff: "code" })).toEqual({ kind: "wrongHost" });
  });

  it("is wrongHost when the session check is refused for the address", async () => {
    const { fetch } = agent({ "GET /api/health": HEALTHY, "GET /api/session": refusal(403, "forbidden_host") });
    expect(await boot({ fetch, handoff: null })).toEqual({ kind: "wrongHost" });
  });
```

Run (from `web/`): `npx vitest run apps/console/src/boot`
Expected: the three wrongHost tests FAIL (they get `notAnswering`).

- [ ] **Step 2: Implement in `boot.ts`**

Add to the `BootResult` union: `| { kind: "wrongHost" }`. Add below the imports:

```ts
function isWrongHost(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 403 &&
    (error.code === "forbidden_host" || error.code === "forbidden_origin")
  );
}
```

Change the three catch blocks:

```ts
  try {
    health = await api.get<{ api?: unknown }>("/api/health");
  } catch (error) {
    return isWrongHost(error) ? { kind: "wrongHost" } : { kind: "notAnswering" };
  }
```

```ts
    } catch (error) {
      if (isWrongHost(error)) return { kind: "wrongHost" };
      if (!(error instanceof ApiError && error.code === "handoff_invalid")) {
        return { kind: "notAnswering" };
      }
```

```ts
  } catch (error) {
    if (isSessionLost(error)) {
      return { kind: "signedOut", reason: spent ? "handoff_spent" : error.code };
    }
    return isWrongHost(error) ? { kind: "wrongHost" } : { kind: "notAnswering" };
  }
```

- [ ] **Step 3: The screen**

`web/apps/console/src/screens/WrongHost.tsx`:

```tsx
import { Button, Egg } from "@eggie/ui";
import { StatusScreen } from "./StatusScreen";
import s from "./StatusScreen.module.css";

export function WrongHost({ onRetry }: { onRetry: () => void }) {
  return (
    <StatusScreen
      art={<Egg tone="cold" size={96} />}
      title="This page was opened from an address Eggie doesn't recognise"
      actions={<Button variant="primary" size="lg" onClick={onRetry}>Try again</Button>}
    >
      <p className={s.lead}>
        Open it as <strong>http://localhost:39080</strong> — the desktop app's <strong>Open Eggie</strong> button
        does that for you.
      </p>
    </StatusScreen>
  );
}
```

In `App.tsx` import it and add the case to the switch:

```tsx
    case "wrongHost":
      return <WrongHost onRetry={run} />;
```

- [ ] **Step 4: Verify**

Run (from `web/`): `npm test && npm run typecheck`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add web/apps/console/src/boot web/apps/console/src/screens/WrongHost.tsx web/apps/console/src/App.tsx
git commit -m "Tell the user when the page was opened from an address the agent refuses"
```

---

### Task 4: Kit — TextField, PromptCard refused copy, Collapsible onToggle

**Files:**
- Create: `web/packages/ui/src/components/TextField.tsx`, `web/packages/ui/src/components/TextField.module.css`
- Modify: `web/packages/ui/src/components/PromptCard.tsx`
- Modify: `web/packages/ui/src/components/Collapsible.tsx`
- Modify: `web/packages/ui/src/index.ts`
- Modify: `web/apps/console/src/screens/Kit.tsx`

**Interfaces:**
- Produces: `TextField({ label: string; value: string; onChange: (value: string) => void; hint?: ReactNode; error?: string; autoFocus?: boolean; placeholder?: string })`; `Collapsible` gains `onToggle?: (open: boolean) => void`.

No tests: these are presentational; `packages/ui/test/boundary.test.ts` already covers the kit's imports.

- [ ] **Step 1: TextField**

`web/packages/ui/src/components/TextField.tsx`:

```tsx
import { useId, type ReactNode } from "react";
import { cx } from "../cx";
import s from "./TextField.module.css";

export function TextField({
  label,
  value,
  onChange,
  hint,
  error,
  autoFocus = false,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: ReactNode;
  error?: string;
  autoFocus?: boolean;
  placeholder?: string;
}) {
  const id = useId();
  const note = error ?? hint;
  return (
    <div className={s.field}>
      <label htmlFor={id} className={s.label}>{label}</label>
      <input
        id={id}
        className={cx(s.input, error !== undefined && s.invalid)}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoFocus={autoFocus}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        aria-invalid={error !== undefined || undefined}
        aria-describedby={note ? `${id}-note` : undefined}
      />
      {note && (
        <p id={`${id}-note`} className={error !== undefined ? s.error : s.hint} role={error !== undefined ? "alert" : undefined}>
          {note}
        </p>
      )}
    </div>
  );
}
```

`web/packages/ui/src/components/TextField.module.css`:

```css
.field { display: flex; flex-direction: column; gap: 7px; }
.label { font: 600 13.5px var(--font-body); color: var(--ink-2); }
.input {
  padding: 12px 14px; border-radius: 13px; border: 1px solid var(--line-2); background: var(--surface);
  font: 500 16px var(--font-body); color: var(--ink);
}
.input:focus-visible { outline: 2px solid var(--yolk-deep); outline-offset: 1px; }
.invalid { border-color: var(--paprika); }
.hint { margin: 0; font-size: 14px; color: var(--ink-2); }
.hint strong { font-family: var(--font-mono); font-weight: 500; color: var(--ink); }
.error { margin: 0; font-size: 14px; color: var(--paprika); }
```

Export from `web/packages/ui/src/index.ts` (keep alphabetical order):

```ts
export { TextField } from "./components/TextField";
```

- [ ] **Step 2: PromptCard says when the clipboard refuses**

In `PromptCard.tsx`: add `const [refused, setRefused] = useState(false);` next to `copied`. In `copy()`, after `setCopied(true);` add `setRefused(false);`; at the start of the `catch` block add `setRefused(true);`. Replace `<span className={s.hint}>{hint}</span>` with:

```tsx
          <span className={s.hint} role={refused ? "alert" : undefined}>
            {refused ? "Couldn't copy — the text is selected, press Ctrl+C (⌘C on a Mac)." : hint}
          </span>
```

- [ ] **Step 3: Collapsible reports when it opens**

In `Collapsible.tsx` add the prop `onToggle?: (open: boolean) => void;` to the props type and destructuring, and on the `<details>` element:

```tsx
      onToggle={onToggle && ((event) => onToggle(event.currentTarget.open))}
```

- [ ] **Step 4: Show TextField in the kit gallery**

In `Kit.tsx` import `TextField` and add, after the "Buttons" section:

```tsx
      <Section title="Text field">
        <div className={s.wide}>
          <TextField label="Name" value="weekend shop" onChange={() => {}} hint={<>It'll be called <strong>weekend-shop</strong></>} />
        </div>
        <div className={s.wide}>
          <TextField label="Name" value="recipe-box" onChange={() => {}} error="There's already a project called recipe-box." />
        </div>
      </Section>
```

- [ ] **Step 5: Verify**

Run (from `web/`): `npm test && npm run typecheck`
Expected: all pass (boundary test included).

- [ ] **Step 6: Commit**

```bash
git add web/packages/ui/src web/apps/console/src/screens/Kit.tsx
git commit -m "Add a text field to the kit and say when copying a prompt is refused"
```

---

### Task 5: Query hooks, plain-text requests, copy and prompts

**Files:**
- Modify: `web/apps/console/src/api/client.ts`
- Modify: `web/apps/console/src/api/client.test.ts`
- Create: `web/apps/console/src/projects/queries.ts`
- Create: `web/apps/console/src/projects/copy.ts`
- Create: `web/apps/console/src/projects/prompts.ts`
- Create: `web/apps/console/src/projects/useNow.ts`
- Create: `web/apps/console/src/components/Elapsed.tsx`

**Interfaces:**
- Consumes: types (Task 1), `Cause` (Task 1), `elapsed` (Task 2).
- Produces:
  - `Api.text(path: string): Promise<string>`
  - `useProjects()`, `useProject(id: string)`, `useJob(jobId: string)`, `useLifecycle(id: string)` (mutate with `"up" | "down" | "restart"`), `useCreateProject()` (mutate with the raw name), `useAdopt()` (mutate with the folder name), `useDeletePreview(id: string, enabled: boolean)`, `useDeleteProject(id: string)` (mutate with `purge: boolean`, resolves `DeleteResult`), `useLogs(id: string, enabled: boolean)`, `projectPath(id: string): string`
  - `phaseCaption(phase: string, firstRun: boolean): string`, `CAUSE_COPY: Record<Cause, { heading: (id: string) => string; short: string; body: string }>`, `actionError(error: unknown): string`, `primaryUrl(project: Project): string | null`
  - `ANALYZE_PROMPT: string`, `fixPrompt(cause: Cause, id: string, detail: string | null): string`, `waitingPrompt(id: string): string`
  - `useNow(everyMs?: number): number`, `<Elapsed startedAt={number} />`

- [ ] **Step 1: Failing tests for `text()`**

Append inside `describe("the API client", …)` in `client.test.ts`:

```ts
  it("returns a plain-text body as it came from text()", async () => {
    const { api } = answering(200, "11:04:19 web  listening on 127.0.0.1:8000\n");
    expect(await api.text("/api/projects/a/logs")).toBe("11:04:19 web  listening on 127.0.0.1:8000\n");
  });

  it("still turns an agent error into an ApiError from text()", async () => {
    const { api } = answering(409, JSON.stringify({ error: { code: "logs_unavailable", message: "no logs" } }));
    const error = await failure(api.text("/api/projects/a/logs"));
    expect([error.code, error.status]).toEqual(["logs_unavailable", 409]);
  });
```

Run (from `web/`): `npx vitest run apps/console/src/api`
Expected: FAIL — `api.text is not a function`.

- [ ] **Step 2: Implement `text()`**

In `client.ts` add `text(path: string): Promise<string>;` to `interface Api`. Replace the body of `createApi` with:

```ts
export function createApi(fetchImpl: typeof fetch, { timeoutMs = 10_000 }: { timeoutMs?: number } = {}): Api {
  async function send(method: string, path: string, body?: unknown): Promise<{ status: number; text: string }> {
    let response: Response;
    try {
      // Origin is left to the browser: the agent refuses a non-GET without it.
      // A hung agent must not hang the page: an aborted fetch rejects and
      // falls into the same "unreachable" mapping below as a refused one.
      response = await fetchImpl(path, {
        method,
        credentials: "same-origin",
        signal: AbortSignal.timeout(timeoutMs),
        ...(body === undefined
          ? {}
          : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
      });
    } catch {
      throw new ApiError("unreachable", "Eggie's service isn't answering", 0);
    }
    const text = await response.text();
    if (!response.ok) {
      const parsed = parse(text);
      const error = parsed.ok ? agentError(parsed.value) : null;
      if (error) throw new ApiError(error.code, error.message, response.status);
      throw new ApiError("unexpected", `unexpected answer (${response.status})`, response.status);
    }
    return { status: response.status, text };
  }

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const { status, text } = await send(method, path, body);
    const parsed = parse(text);
    if (!parsed.ok) throw new ApiError("unexpected", "the answer wasn't JSON", status);
    return parsed.value as T;
  }

  return {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
    del: (path) => request("DELETE", path),
    text: async (path) => (await send("GET", path)).text,
  };
}
```

Run: `npx vitest run apps/console/src/api` — Expected: all pass (old tests included).

- [ ] **Step 3: Clock hook and Elapsed**

`web/apps/console/src/projects/useNow.ts`:

```ts
import { useEffect, useState } from "react";

export function useNow(everyMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(timer);
  }, [everyMs]);
  return now;
}
```

`web/apps/console/src/components/Elapsed.tsx`:

```tsx
import { elapsed } from "../projects/format";
import { useNow } from "../projects/useNow";

export function Elapsed({ startedAt }: { startedAt: number }) {
  const now = useNow();
  return <>{elapsed(startedAt, now)}</>;
}
```

- [ ] **Step 4: Query hooks**

`web/apps/console/src/projects/queries.ts`:

```ts
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DeletePreview, DeleteResult, Job, Project, ProjectList } from "./types";

const BUSY_MS = 3000;
const IDLE_MS = 15_000;
const JOB_MS = 1000;

const ALL = ["projects"] as const;
const LIST = ["projects", "list"] as const;
const ONE = (id: string) => ["projects", "one", id] as const;

export function projectPath(id: string): string {
  return `/api/projects/${encodeURIComponent(id)}`;
}

export function useProjects() {
  return useQuery({
    queryKey: LIST,
    queryFn: () => api.get<ProjectList>("/api/projects"),
    refetchInterval: (query) =>
      query.state.data?.projects.some((project) => project.job !== null) ? BUSY_MS : IDLE_MS,
  });
}

export function useProject(id: string) {
  return useQuery({
    queryKey: ONE(id),
    queryFn: () => api.get<Project>(projectPath(id)),
    refetchInterval: (query) => (query.state.data?.job ? BUSY_MS : IDLE_MS),
  });
}

export function useJob(jobId: string) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["jobs", jobId],
    queryFn: () => api.get<Job>(`/api/jobs/${encodeURIComponent(jobId)}`),
    refetchInterval: (q) => (q.state.error || (q.state.data && q.state.data.state !== "running") ? false : JOB_MS),
  });
  // A finished or forgotten job (the agent restarted) means the project has
  // moved on; refetch it now rather than at the next poll.
  const settled = query.isError || (query.data !== undefined && query.data.state !== "running");
  useEffect(() => {
    if (settled) void client.invalidateQueries({ queryKey: ALL });
  }, [settled, client]);
  return query;
}

export function useLifecycle(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (action: "up" | "down" | "restart") => api.post<{ job_id: string }>(`${projectPath(id)}/${action}`),
    onSettled: () => client.invalidateQueries({ queryKey: ALL }),
  });
}

export function useCreateProject() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.post<Project>("/api/projects", { id: name }),
    onSuccess: () => client.invalidateQueries({ queryKey: LIST }),
  });
}

export function useAdopt() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.post<Project>(`${projectPath(name)}/adopt`),
    onSettled: () => client.invalidateQueries({ queryKey: LIST }),
  });
}

export function useDeletePreview(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["delete-preview", id],
    queryFn: () => api.get<DeletePreview>(`${projectPath(id)}/delete-preview`),
    enabled,
    staleTime: 0,
    gcTime: 0,
  });
}

export function useDeleteProject(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (purge: boolean) => api.del<DeleteResult>(`${projectPath(id)}${purge ? "?purge=true" : ""}`),
    onSuccess: () => client.invalidateQueries({ queryKey: LIST }),
  });
}

export function useLogs(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["logs", id],
    queryFn: () => api.text(`${projectPath(id)}/logs`),
    enabled,
  });
}
```

- [ ] **Step 5: Copy and prompts**

`web/apps/console/src/projects/copy.ts`:

```ts
import { ApiError } from "../api/client";
import type { Project } from "./types";
import type { Cause } from "./view";

export function phaseCaption(phase: string, firstRun: boolean): string {
  switch (phase) {
    case "preparing":
      return "Getting the pan out";
    case "starting":
      return firstRun ? "Fetching the bits it needs" : "Starting it up";
    case "checking":
      return "Checking it answers";
    default:
      return "Working on it";
  }
}

export const CAUSE_COPY: Record<Cause, { heading: (id: string) => string; short: string; body: string }> = {
  bound_to_loopback: {
    heading: (id) => `${id} started, but it isn't answering`,
    short: "Started, but it isn't answering the door",
    body:
      "Your app is awake — it's just talking to itself. It needs to listen for visitors coming from outside its own " +
      "box, and right now it only listens to the inside. Your coding agent can change that in one line.",
  },
  service_unreachable: {
    heading: (id) => `${id} started, but nothing answers at its address`,
    short: "Started, but nothing answers at its address",
    body:
      "Its little machines are up, but nothing replies where Eggie sends visitors. It may be listening on a " +
      "different port, or it fell over after starting.",
  },
  crash_looping: {
    heading: (id) => `${id} keeps falling over`,
    short: "Keeps falling over",
    body: "It starts, trips, and starts again. The raw details below usually say why — your coding agent can read them.",
  },
  failed_to_start: {
    heading: (id) => `${id} couldn't start`,
    short: "Couldn't start",
    body: "Docker refused to start it. The raw details below say what it tripped on.",
  },
  unreadable: {
    heading: (id) => `Eggie can't read ${id}'s start-up recipe`,
    short: "Eggie can't read its start-up recipe",
    body: "The docker-compose.yml is there, but Eggie can't make sense of it.",
  },
};

export function actionError(error: unknown): string {
  if (error instanceof ApiError && error.code === "project_busy") return "It's already busy — give it a moment.";
  return error instanceof Error ? error.message : "Something went wrong.";
}

export function primaryUrl(project: Project): string | null {
  return project.web.find((entry) => entry.primary)?.url ?? project.urls[0] ?? null;
}
```

`web/apps/console/src/projects/prompts.ts`:

```ts
import type { Cause } from "./view";

export const ANALYZE_PROMPT = `Take a look around this project and tell me, in plain
language: what it does today, what's half-finished or
broken, and the three things you'd fix first. Don't
change any code yet — just tell me what you found.`;

const CLOSE = "Keep the change as small as you can, then tell me in plain words what you changed.";

export function fixPrompt(cause: Cause, id: string, detail: string | null): string {
  const where = `The project in ~/projects/${id}`;
  switch (cause) {
    case "bound_to_loopback":
      return `${where} starts, but its web service only listens on 127.0.0.1 inside its container, so nothing outside the container can reach it. Make it listen on 0.0.0.0 instead. ${CLOSE}`;
    case "service_unreachable":
      return `${where} starts, but nothing answers on the port its web service is published on. Check that the port in docker-compose.yml is the one the app really listens on, and that the app doesn't exit after starting. ${CLOSE}`;
    case "crash_looping":
      return `${where} keeps restarting: one of its containers exits and starts again. Read its logs with docker compose logs in that folder, find why it exits, and fix it. ${CLOSE}`;
    case "failed_to_start":
      return `${where} won't start: Docker refuses to bring it up. Find out why from docker-compose.yml and the error Docker gives, and fix it. ${CLOSE}`;
    case "unreadable":
      return `${where} has a docker-compose.yml that Eggie can't read${detail ? ` (it says: ${detail})` : ""}. Make it a valid compose file that starts the project, with the web service listening on 0.0.0.0. ${CLOSE}`;
  }
}

export function waitingPrompt(id: string): string {
  return `I want to build something in ~/projects/${id}. Ask me what it should do, then build it, and add a docker-compose.yml at the top of that folder that starts it, with the web service listening on 0.0.0.0. Tell me in plain words what you made and how to use it.`;
}
```

- [ ] **Step 6: Verify**

Run (from `web/`): `npm test && npm run typecheck`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add web/apps/console/src
git commit -m "Wrap the agent's project routes in query hooks, with the screens' copy and prompts"
```

---

### Task 6: Stateful mock agent

**Files:**
- Modify: `web/apps/console/src/mocks/handlers.ts` (full rewrite)

**Interfaces:**
- Consumes: types (Task 1), `slugify` (Task 2).
- Produces: `SCENARIOS` now `["ok", "empty", "expired", "handoff-spent", "old-agent", "down", "lost-mid-use", "wrong-host"]`; `scenarioFrom` and `handlersFor` keep their signatures (`mocks/browser.ts` is unchanged).

No tests: this is a dev-only test double. Verification is typecheck + the manual walk.

- [ ] **Step 1: Rewrite `handlers.ts`**

```ts
import { delay, http, HttpResponse } from "msw";
import { slugify } from "../projects/slugify";
import type { Discovered, Job, JobKind, Project } from "../projects/types";

export const SCENARIOS = [
  "ok",
  "empty",
  "expired",
  "handoff-spent",
  "old-agent",
  "down",
  "lost-mid-use",
  "wrong-host",
] as const;
export type Scenario = (typeof SCENARIOS)[number];

export function scenarioFrom(search: string): Scenario {
  const wanted = new URLSearchParams(search).get("scenario");
  return SCENARIOS.find((scenario) => scenario === wanted) ?? "ok";
}

const DOMAIN = "127-0-0-1.sslip.io";
const address = (id: string, sub?: string) => `http://${sub ? `${sub}.` : ""}${id}.${DOMAIN}:39080`;
const nowSec = () => Date.now() / 1000;

const LOOPBACK = { code: "bound_to_loopback", message: "the web service listens on 127.0.0.1 inside its container" };
const NO_COMPOSE = { code: "compose_missing", message: "there's no docker-compose.yml in this project yet" };

function project(id: string, over: Partial<Project> = {}): Project {
  return {
    id,
    status: "stopped",
    domain: DOMAIN,
    path: `/opt/eggie/projects/${id}`,
    urls: [address(id)],
    problem: null,
    empty: false,
    web: [{ url: address(id), service: "web", primary: true }],
    first_run: false,
    job: null,
    ...over,
  };
}

function emptyProject(id: string): Project {
  return project(id, { urls: [], web: [], empty: true, problem: NO_COMPOSE, first_run: true });
}

const LOGS = `11:04:19 photo-sorter_web  listening on 127.0.0.1:8000
11:04:22 proxy  GET / → ECONNREFUSED 172.19.0.4:8000
11:04:27 proxy  health check failed 3/3
`;

// Same codes and wording as the agent's own refusals.
const refuse = (code: string, message: string, status: number) =>
  HttpResponse.json({ error: { code, message } }, { status });
const notSignedIn = () => refuse("not_signed_in", "open Eggie from the desktop app to sign in", 401);
const expired = () => refuse("session_expired", "your sign-in ran out; open Eggie from the desktop app again", 401);
const notFound = (id: string) => refuse("project_not_found", `no project called ${id}`, 404);
const busy = () => refuse("project_busy", "this project is busy with another job", 409);

export function handlersFor(scenario: Scenario) {
  let signedIn = scenario === "ok" || scenario === "empty" || scenario === "old-agent" || scenario === "lost-mid-use";
  const projects = new Map<string, Project>();
  const discovered: Discovered[] = [];
  const jobs = new Map<string, Job>();

  function startJob(target: Project, kind: JobKind): Job {
    const id = Math.random().toString(16).slice(2, 14);
    const steps = kind === "down" ? ["preparing", "stopping"] : ["preparing", "starting", "checking"];
    const stepMs = target.first_run && kind !== "down" ? 6000 : 2000;
    const job: Job = {
      job_id: id,
      kind,
      phase: steps[0],
      state: "running",
      detail: "",
      result: null,
      started_at: nowSec(),
      finished_at: null,
    };
    jobs.set(id, job);
    target.job = { id, kind, phase: steps[0], started_at: job.started_at };
    let step = 0;
    const tick = () => {
      step += 1;
      if (step < steps.length) {
        job.phase = steps[step];
        if (target.job) target.job = { ...target.job, phase: job.phase };
        window.setTimeout(tick, stepMs);
        return;
      }
      target.job = null;
      job.finished_at = nowSec();
      if (kind === "down") {
        target.status = "stopped";
        job.state = "done";
        job.result = { status: "stopped" };
        return;
      }
      target.first_run = false;
      target.status = "started_ok";
      // photo-sorter never gets better, so "Try again" can be walked.
      target.problem = target.id === "photo-sorter" ? LOOPBACK : null;
      job.state = target.problem ? "failed" : "done";
      job.detail = target.problem ? target.problem.message : "";
      job.result = { status: target.status, urls: target.urls, problem: target.problem };
    };
    window.setTimeout(tick, stepMs);
    return job;
  }

  if (scenario !== "empty") {
    const minutesAgo = (n: number) => nowSec() - n * 60;
    for (const p of [
      project("recipe-box", {
        status: "started_ok",
        urls: [address("recipe-box"), address("recipe-box", "api"), address("recipe-box", "studio")],
        web: [
          { url: address("recipe-box"), service: "web", primary: true },
          { url: address("recipe-box", "api"), service: "api", primary: false },
          { url: address("recipe-box", "studio"), service: "studio", primary: false },
        ],
      }),
      project("weekend-shop"),
      project("tiny-crm", { first_run: true }),
      project("photo-sorter", { status: "started_ok", problem: LOOPBACK }),
      emptyProject("notes-app"),
      project("old-blog", { urls: [], web: [], problem: { code: "folder_missing", message: "this project's folder is gone" } }),
    ]) {
      projects.set(p.id, p);
    }
    startJob(projects.get("tiny-crm")!, "up");
    discovered.push(
      { name: "invoice-helper", seen_at: minutesAgo(4), adoptable: true, reason: null },
      { name: "spice-rack", seen_at: minutesAgo(20), adoptable: false, reason: "compose_missing" },
      { name: "Tax Stuff", seen_at: minutesAgo(130), adoptable: false, reason: "bad_name" },
    );
  }

  const guard = () => (signedIn ? null : notSignedIn());
  const find = (id: string) => projects.get(id) ?? null;

  const lifecycle = (action: "up" | "down" | "restart") =>
    http.post(`/api/projects/:id/${action}`, ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = find(String(params.id));
      if (!target) return notFound(String(params.id));
      if (target.job) return busy();
      if (action !== "down" && target.empty) return refuse(NO_COMPOSE.code, NO_COMPOSE.message, 400);
      return HttpResponse.json({ job_id: startJob(target, action).job_id }, { status: 202 });
    });

  return [
    http.get("/api/health", () => {
      if (scenario === "down") return HttpResponse.error();
      if (scenario === "wrong-host") return refuse("forbidden_host", "this address isn't allowed", 403);
      return HttpResponse.json({
        status: "ok",
        version: "0.2.0",
        api: scenario === "old-agent" ? 2 : 1,
        docker: { reachable: true, version: "29.0.0", detail: "" },
      });
    }),
    http.post("/api/session", () => {
      if (scenario === "handoff-spent") {
        return refuse("handoff_invalid", "that sign-in link has already been used or has run out; open Eggie from the desktop app again", 401);
      }
      signedIn = true;
      return HttpResponse.json({ signed_in: true });
    }),
    http.get("/api/session", () => {
      if (signedIn) return HttpResponse.json({ signed_in: true });
      return scenario === "expired" ? expired() : notSignedIn();
    }),
    http.delete("/api/session", () => {
      signedIn = false;
      return HttpResponse.json({ signed_in: false });
    }),

    http.get("/api/projects", async () => {
      if (scenario === "lost-mid-use") return expired();
      const denied = guard();
      if (denied) return denied;
      await delay(200);
      return HttpResponse.json({ projects: [...projects.values()], discovered });
    }),
    http.post("/api/projects", async ({ request }) => {
      const denied = guard();
      if (denied) return denied;
      const { id: raw } = (await request.json()) as { id: string };
      const id = slugify(raw);
      if (!id) return refuse("invalid_project", "a project needs a name made of letters, numbers or dashes", 422);
      if (projects.has(id)) return refuse("project_exists", `a project called ${id} already exists`, 409);
      const created = emptyProject(id);
      projects.set(id, created);
      return HttpResponse.json(created, { status: 201 });
    }),
    http.get("/api/projects/:id", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = find(String(params.id));
      return target ? HttpResponse.json(target) : notFound(String(params.id));
    }),
    http.post("/api/projects/:id/adopt", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const name = String(params.id);
      const index = discovered.findIndex((folder) => folder.name === name);
      if (projects.has(name)) return refuse("project_exists", `a project called ${name} already exists`, 409);
      if (index === -1) return refuse("folder_not_found", `there's no folder called ${name}`, 404);
      if (!discovered[index].adoptable) return refuse("not_adoptable", `${name} can't be adopted yet`, 409);
      discovered.splice(index, 1);
      const adopted = project(name, { first_run: true });
      projects.set(name, adopted);
      return HttpResponse.json(adopted, { status: 201 });
    }),
    lifecycle("up"),
    lifecycle("down"),
    lifecycle("restart"),
    http.get("/api/jobs/:id", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const job = jobs.get(String(params.id));
      return job ? HttpResponse.json(job) : refuse("job_not_found", "no such job", 404);
    }),
    http.get("/api/projects/:id/logs", async ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = find(String(params.id));
      if (!target) return notFound(String(params.id));
      await delay(300);
      if (target.empty) return refuse("logs_unavailable", "there are no logs for this project yet", 409);
      const text = target.id === "photo-sorter" ? LOGS : `${target.id}_web  ready\n`;
      return new HttpResponse(text, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
    }),
    http.get("/api/projects/:id/delete-preview", async ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = find(String(params.id));
      if (!target) return notFound(String(params.id));
      await delay(400);
      if (target.empty) return HttpResponse.json({ files: 0, bytes: 0, containers: [], volumes: [] });
      return HttpResponse.json({
        files: 412,
        bytes: 38 * 1024 * 1024,
        containers: [`${target.id}-web-1`, `${target.id}-db-1`],
        volumes: [`${target.id}_db-data`],
      });
    }),
    http.delete("/api/projects/:id", ({ params }) => {
      const denied = guard();
      if (denied) return denied;
      const target = find(String(params.id));
      if (!target) return notFound(String(params.id));
      if (target.job) return busy();
      projects.delete(target.id);
      // photo-sorter shows the "may still be running" outcome.
      const stopped = target.id !== "photo-sorter";
      return HttpResponse.json({ id: target.id, stopped, detail: stopped ? "" : "a container didn't stop in time" });
    }),
  ];
}
```

- [ ] **Step 2: Verify**

Run (from `web/`): `npm run typecheck && npm test && npm run build && npm run check-offline`
Expected: all pass (the mocks are only imported in development, so the offline check still passes).

- [ ] **Step 3: Commit**

```bash
git add web/apps/console/src/mocks/handlers.ts
git commit -m "Make the dev mock agent keep projects, run jobs through their phases and delete"
```

---

### Task 7: Projects list, discovered band, new project

**Files:**
- Create: `web/apps/console/src/screens/list/ProjectList.tsx`, `ProjectList.module.css`
- Create: `web/apps/console/src/screens/list/ProjectRow.tsx`
- Create: `web/apps/console/src/screens/list/DiscoveredBand.tsx`
- Create: `web/apps/console/src/screens/list/NewProjectModal.tsx`
- Create: `web/apps/console/src/screens/list/EmptyCounter.tsx`
- Create: `web/apps/console/src/screens/icons.tsx`
- Delete: `web/apps/console/src/screens/Projects.tsx`, `web/apps/console/src/screens/Projects.module.css`
- Modify: `web/apps/console/src/App.tsx`

**Interfaces:**
- Consumes: Tasks 1, 2, 4, 5.
- Produces: `ProjectList` (route `/`); `NewProjectModal({ open, onClose })`; icons `PLUS`, `ARROW`, `FOLDER`, `MAGNIFIER`, `GLOBE`, `TRASH`; list location state `{ deleted?: DeleteResult }` (read by the list, written by Tasks 8 and 9).

No tests: screens are glue over tested modules; the human walks them.

- [ ] **Step 1: Icons**

`web/apps/console/src/screens/icons.tsx`:

```tsx
const svg = { width: 16, height: 16, viewBox: "0 0 18 18", fill: "none", "aria-hidden": true } as const;
const line = { stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" } as const;

export const PLUS = <svg {...svg}><path d="M9 3.5v11M3.5 9h11" {...line} /></svg>;
export const ARROW = <svg {...svg}><path d="M6 12 12 6M7 5.5h5.5V11" {...line} /></svg>;
export const FOLDER = (
  <svg {...svg}><path d="M2.5 5.2A1.5 1.5 0 0 1 4 3.7h3l1.4 1.8H14A1.5 1.5 0 0 1 15.5 7v6.2A1.5 1.5 0 0 1 14 14.7H4a1.5 1.5 0 0 1-1.5-1.5V5.2Z" {...line} /></svg>
);
export const MAGNIFIER = <svg {...svg}><circle cx="8" cy="8" r="4.8" {...line} /><path d="m11.6 11.6 3.4 3.4" {...line} /></svg>;
export const GLOBE = (
  <svg {...svg}><circle cx="9" cy="9" r="6.5" {...line} /><path d="M2.5 9h13M9 2.5c2 2.2 2 10.8 0 13M9 2.5c-2 2.2-2 10.8 0 13" {...line} /></svg>
);
export const TRASH = <svg {...svg}><path d="M3.5 5h11M7.2 5V3.5h3.6V5M5 5l.7 9.5h6.6L13 5" {...line} /></svg>;
```

- [ ] **Step 2: Styles**

`web/apps/console/src/screens/list/ProjectList.module.css`:

```css
.page { display: flex; flex-direction: column; gap: 18px; }
.head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.title { margin: 0; font: 800 34px var(--font-display); letter-spacing: -.025em; color: var(--ink); }
.sub { margin: 4px 0 0; font-size: 15px; color: var(--ink-2); }
.muted { margin: 0; font-size: 15px; color: var(--ink-3); }
.error { margin: 0; font-size: 15px; color: var(--paprika); }
.list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 10px; }

.row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.who { flex: 1; min-width: 180px; display: flex; flex-direction: column; gap: 3px; }
.name { font: 700 18px var(--font-body); color: var(--ink); }
.address { font: 400 13px var(--font-mono); }
.addressMuted { font: 400 13px var(--font-mono); color: var(--ink-3); }
.trouble { font-size: 14px; color: var(--paprika); }
.quiet { font-size: 14px; color: var(--ink-3); }
.actions { display: flex; align-items: center; gap: 8px; justify-content: flex-end; }

.band { display: flex; flex-direction: column; gap: 12px; padding: 16px; border-radius: 20px; background: var(--yolk-soft); }
.bandHead { display: flex; gap: 10px; align-items: flex-start; color: var(--yolk-deep); }
.bandTitle { margin: 0; font: 700 17px var(--font-body); color: var(--ink); }
.bandSub { margin: 2px 0 0; font-size: 14px; color: var(--ink-2); }
.folders { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; }
.folder { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.folderText { flex: 1; min-width: 200px; display: flex; flex-direction: column; gap: 4px; }
.blocked { border-style: dashed; background: transparent; }
.recipe { margin: 0; font: 400 12.5px/1.6 var(--font-mono); color: var(--ink-2); white-space: pre; }

.empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 22px; text-align: center; }
.emptyTitle { margin: 0; font: 800 34px var(--font-display); letter-spacing: -.025em; }
.lead { margin: 0; font-size: 16.5px; line-height: 1.5; color: var(--ink-2); max-width: 460px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; width: min(620px, 100%); text-align: left; }
.card { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.cardTitle { margin: 0; display: flex; align-items: center; gap: 8px; font: 700 17px var(--font-body); }
.soon { padding: 2px 8px; border-radius: 99px; background: var(--cold-soft); font: 700 11px var(--font-body); letter-spacing: .06em; text-transform: uppercase; color: var(--ink-2); }
.cardBody { margin: 0; font-size: 14.5px; line-height: 1.45; color: var(--ink-2); flex: 1; }

.form { display: flex; flex-direction: column; gap: 18px; }
.formActions { display: flex; gap: 10px; }

@media (min-width: 1040px) {
  .row { display: grid; grid-template-columns: 1fr 200px 200px; }
}
```

- [ ] **Step 3: ProjectRow**

`web/apps/console/src/screens/list/ProjectRow.tsx`:

```tsx
import type { ReactNode } from "react";
import { useNavigate } from "react-router";
import { Button, Notice, RowCard, StateBadge } from "@eggie/ui";
import { Elapsed } from "../../components/Elapsed";
import { CAUSE_COPY, actionError, primaryUrl } from "../../projects/copy";
import { hostOf } from "../../projects/format";
import { useLifecycle } from "../../projects/queries";
import type { Project } from "../../projects/types";
import { projectView } from "../../projects/view";
import { ARROW } from "../icons";
import s from "./ProjectList.module.css";

export function ProjectRow({ project }: { project: Project }) {
  const view = projectView(project);
  const lifecycle = useLifecycle(project.id);
  const navigate = useNavigate();
  const primary = primaryUrl(project);
  const page = `/p/${encodeURIComponent(project.id)}`;
  const look = () => navigate(page);

  let line: ReactNode = null;
  let actions: ReactNode = null;
  switch (view.kind) {
    case "running":
      line = primary && <a className={s.address} href={primary} target="_blank" rel="noopener noreferrer">{hostOf(primary)}</a>;
      actions = (
        <>
          {primary && <Button onClick={() => window.open(primary, "_blank", "noopener,noreferrer")}>Open{ARROW}</Button>}
          <Button variant="quiet" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("down")}>Stop</Button>
        </>
      );
      break;
    case "stopped":
      line = primary && <span className={s.addressMuted}>{hostOf(primary)}</span>;
      actions = <Button disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("up")}>Start</Button>;
      break;
    case "starting":
      line = primary && <span className={s.addressMuted}>{hostOf(primary)}</span>;
      actions = <span className={s.quiet}><Elapsed startedAt={view.job.started_at} /> so far</span>;
      break;
    case "stopping":
      line = <span className={s.quiet}>Putting it away…</span>;
      break;
    case "wrong":
      line = <span className={s.trouble}>{CAUSE_COPY[view.cause].short}</span>;
      actions = <Button variant="danger" onClick={look}>Take a look</Button>;
      break;
    case "gone":
      line = <span className={s.trouble}>Its folder has gone missing</span>;
      actions = <Button variant="danger" onClick={look}>Take a look</Button>;
      break;
    case "waiting":
      line = <span className={s.quiet}>Waiting for your coding agent</span>;
      actions = <Button onClick={look}>Open page</Button>;
      break;
  }

  return (
    <>
      <RowCard accent={view.badge === "wrong" ? "trouble" : "plain"} className={s.row}>
        <div className={s.who}>
          <a className={s.name} href={page} onClick={(event) => { event.preventDefault(); look(); }}>{project.id}</a>
          {line}
        </div>
        <StateBadge state={view.badge} />
        <div className={s.actions}>{actions}</div>
      </RowCard>
      {lifecycle.error && <Notice>{actionError(lifecycle.error)}</Notice>}
    </>
  );
}
```

- [ ] **Step 4: DiscoveredBand**

`web/apps/console/src/screens/list/DiscoveredBand.tsx`:

```tsx
import { useNavigate } from "react-router";
import { Button, Collapsible, Notice, RowCard, cx } from "@eggie/ui";
import { folderHeading, relativeTime } from "../../projects/format";
import { useAdopt } from "../../projects/queries";
import { slugify } from "../../projects/slugify";
import type { Discovered } from "../../projects/types";
import { useNow } from "../../projects/useNow";
import { FOLDER } from "../icons";
import s from "./ProjectList.module.css";

function Folder({ folder, now, adopting, onAdopt }: { folder: Discovered; now: number; adopting: boolean; onAdopt: () => void }) {
  const seen = `Turned up ${relativeTime(folder.seen_at, now)}`;
  if (folder.adoptable) {
    return (
      <RowCard className={s.folder}>
        <div className={s.folderText}>
          <span className={s.name}>{folder.name}</span>
          <span className={s.quiet}>{seen} · knows how to start itself</span>
        </div>
        <Button variant="primary" disabled={adopting} onClick={onAdopt}>Adopt it</Button>
      </RowCard>
    );
  }
  const rename = slugify(folder.name);
  return (
    <RowCard className={cx(s.folder, s.blocked)}>
      <div className={s.folderText}>
        <span className={s.name}>{folder.name}</span>
        {folder.reason === "bad_name" ? (
          <span className={s.quiet}>
            Its name has characters an address can't use — ask your coding agent to rename the folder
            {rename ? <> to <strong>{rename}</strong></> : null}.
          </span>
        ) : (
          <>
            <span className={s.quiet}>
              Eggie can't take this one in yet — the folder doesn't say how to run itself. Ask your coding agent to add
              the start-up recipe and it'll show up here, ready to adopt.
            </span>
            <Collapsible summary="What Eggie looks for">
              <pre className={s.recipe}>{`~/projects/${folder.name}\n  docker-compose.yml  — missing`}</pre>
            </Collapsible>
          </>
        )}
      </div>
      <Button disabled>Can't adopt</Button>
    </RowCard>
  );
}

export function DiscoveredBand({ folders }: { folders: Discovered[] }) {
  const adopt = useAdopt();
  const navigate = useNavigate();
  const now = useNow(30_000);
  return (
    <section className={s.band}>
      <header className={s.bandHead}>
        {FOLDER}
        <div>
          <h2 className={s.bandTitle}>{folderHeading(folders.length)}</h2>
          <p className={s.bandSub}>Your coding agent made these. Eggie hasn't met them yet.</p>
        </div>
      </header>
      <ul className={s.folders}>
        {folders.map((folder) => (
          <li key={folder.name}>
            <Folder
              folder={folder}
              now={now}
              adopting={adopt.isPending && adopt.variables === folder.name}
              onAdopt={() =>
                adopt.mutate(folder.name, { onSuccess: (created) => navigate(`/p/${encodeURIComponent(created.id)}`) })
              }
            />
          </li>
        ))}
      </ul>
      {adopt.error && <Notice>{adopt.error.message}</Notice>}
    </section>
  );
}
```

- [ ] **Step 5: NewProjectModal**

`web/apps/console/src/screens/list/NewProjectModal.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { Button, Modal, TextField } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { useCreateProject } from "../../projects/queries";
import { slugify } from "../../projects/slugify";
import s from "./ProjectList.module.css";

export function NewProjectModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const create = useCreateProject();
  const navigate = useNavigate();
  const slug = slugify(name);

  const close = () => {
    setName("");
    create.reset();
    onClose();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!slug || create.isPending) return;
    create.mutate(name, {
      onSuccess: (created) => {
        close();
        navigate(`/p/${encodeURIComponent(created.id)}`);
      },
    });
  };

  const error = create.error
    ? create.error instanceof ApiError && create.error.code === "project_exists"
      ? `There's already a project called ${slug}.`
      : create.error.message
    : undefined;

  return (
    <Modal open={open} onClose={close} title="Name your project">
      <form className={s.form} onSubmit={submit}>
        <TextField
          label="Name"
          value={name}
          autoFocus
          onChange={(value) => {
            setName(value);
            if (create.isError) create.reset();
          }}
          hint={slug ? <>It'll be called <strong>{slug}</strong></> : undefined}
          error={error}
        />
        <div className={s.formActions}>
          <Button type="submit" variant="primary" disabled={!slug || create.isPending}>Make it</Button>
          <Button variant="quiet" onClick={close}>Keep it for later</Button>
        </div>
      </form>
    </Modal>
  );
}
```

- [ ] **Step 6: EmptyCounter**

`web/apps/console/src/screens/list/EmptyCounter.tsx`:

```tsx
import { Button, Egg, Notice, RowCard } from "@eggie/ui";
import { PLUS } from "../icons";
import s from "./ProjectList.module.css";

export function EmptyCounter({ onNew }: { onNew: () => void }) {
  return (
    <section className={s.empty}>
      <Egg size={72} />
      <h1 className={s.emptyTitle}>An empty counter</h1>
      <p className={s.lead}>Projects live here. Make one and your coding agent finally has somewhere to put things.</p>
      <div className={s.cards}>
        <RowCard className={s.card}>
          <h2 className={s.cardTitle}>Start from scratch</h2>
          <p className={s.cardBody}>An empty project with a name on it. Your coding agent takes it from there.</p>
          <Button variant="primary" onClick={onNew}>{PLUS}New project</Button>
        </RowCard>
        <RowCard className={s.card}>
          <h2 className={s.cardTitle}>From GitHub <span className={s.soon}>Soon</span></h2>
          <p className={s.cardBody}>Pull in a repo you already have. We're still building this one.</p>
          <Button disabled>Not yet</Button>
        </RowCard>
      </div>
      <Notice icon="folder">Already have a folder on your computer? The desktop app carries it in for you.</Notice>
    </section>
  );
}
```

- [ ] **Step 7: ProjectList**

`web/apps/console/src/screens/list/ProjectList.tsx`:

```tsx
import { useState } from "react";
import { useLocation } from "react-router";
import { Button, Notice } from "@eggie/ui";
import { subtitle } from "../../projects/format";
import { useProjects } from "../../projects/queries";
import type { DeleteResult } from "../../projects/types";
import { projectView } from "../../projects/view";
import { PLUS } from "../icons";
import { DiscoveredBand } from "./DiscoveredBand";
import { EmptyCounter } from "./EmptyCounter";
import { NewProjectModal } from "./NewProjectModal";
import { ProjectRow } from "./ProjectRow";
import s from "./ProjectList.module.css";

function DeletedNotice({ result }: { result: DeleteResult }) {
  return result.stopped ? (
    <Notice>Threw out {result.id}.</Notice>
  ) : (
    <Notice>
      {result.id} is deleted. Some of its little machines may still be running — restarting Eggie from the desktop
      app clears them.
    </Notice>
  );
}

export function ProjectList() {
  const query = useProjects();
  const [creating, setCreating] = useState(false);
  const deleted = (useLocation().state as { deleted?: DeleteResult } | null)?.deleted;
  const modal = <NewProjectModal open={creating} onClose={() => setCreating(false)} />;

  if (query.data === undefined) {
    return query.isError ? <p className={s.error}>{query.error.message}</p> : <p className={s.muted}>Checking the kitchen…</p>;
  }

  const { projects, discovered } = query.data;
  if (projects.length === 0 && discovered.length === 0) {
    return (
      <>
        {deleted && <DeletedNotice result={deleted} />}
        <EmptyCounter onNew={() => setCreating(true)} />
        {modal}
      </>
    );
  }

  const cooking = projects.filter((project) => projectView(project).kind === "running").length;
  return (
    <section className={s.page}>
      {deleted && <DeletedNotice result={deleted} />}
      {query.isError && <Notice>{query.error.message}</Notice>}
      <header className={s.head}>
        <div>
          <h1 className={s.title}>Your projects</h1>
          <p className={s.sub}>{subtitle(projects.length, cooking)}</p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>{PLUS}New project</Button>
      </header>
      {discovered.length > 0 && <DiscoveredBand folders={discovered} />}
      {projects.length > 0 && (
        <ul className={s.list}>
          {projects.map((project) => (
            <li key={project.id}><ProjectRow project={project} /></li>
          ))}
        </ul>
      )}
      <Notice>These addresses only work on this computer. Nothing is out on the internet.</Notice>
      {modal}
    </section>
  );
}
```

- [ ] **Step 8: Routes**

Delete `screens/Projects.tsx` and `screens/Projects.module.css`. In `App.tsx` replace the `Projects` import with `import { ProjectList } from "./screens/list/ProjectList";`, add `Navigate` to the `react-router` import, and replace the `<Routes>` block with:

```tsx
              <Routes>
                <Route path="/" element={<ProjectList />} />
                <Route path="/kit" element={<Kit />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
```

- [ ] **Step 9: Verify**

Run (from `web/`): `npm run typecheck && npm test && npm run build && npm run check-offline`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add -A web/apps/console/src
git commit -m "List projects with their states, found folders and a way to make a new one"
```

---

### Task 8: Project page, Analyze, fix prompt

**Files:**
- Create: `web/apps/console/src/screens/project/ProjectPage.tsx`, `ProjectPage.module.css`
- Create: `web/apps/console/src/screens/project/StartingBody.tsx`
- Create: `web/apps/console/src/screens/project/WrongBody.tsx`
- Create: `web/apps/console/src/screens/project/AddressRows.tsx`
- Create: `web/apps/console/src/screens/project/Tiles.tsx`
- Create: `web/apps/console/src/screens/project/AnalyzeModal.tsx`
- Modify: `web/apps/console/src/App.tsx`

**Interfaces:**
- Consumes: Tasks 1, 2, 4, 5, icons from Task 7.
- Produces: `ProjectPage` (route `/p/:id`) holding `const [modal, setModal] = useState<"analyze" | "delete" | null>(null)`; `Tiles({ onAnalyze, onDelete })`. Task 9 renders the delete modal where marked.

No tests: screens are glue over tested modules.

- [ ] **Step 1: Styles**

`web/apps/console/src/screens/project/ProjectPage.module.css`:

```css
.page { display: flex; flex-direction: column; gap: 20px; max-width: 880px; width: 100%; margin: 0 auto; }
.back { align-self: flex-start; font: 600 14px var(--font-body); color: var(--ink-2); }
.head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.name { margin: 0; font: 800 32px var(--font-display); letter-spacing: -.025em; color: var(--ink); }
.sub { margin: 2px 0 0; font-size: 14.5px; color: var(--ink-2); }
.grow { flex: 1; min-width: 0; }
.actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.note { font-size: 14px; color: var(--ink-3); }
.muted { margin: 0; font-size: 15px; color: var(--ink-3); }
.error { margin: 0; font-size: 15px; color: var(--paprika); }
.lead { margin: 0; font-size: 16.5px; line-height: 1.55; color: var(--ink-2); max-width: 600px; text-wrap: pretty; }
.detail { margin: 0; font: 400 13px var(--font-mono); color: var(--ink-3); }
.big { margin: 0; font: 800 30px/1.1 var(--font-display); letter-spacing: -.02em; color: var(--ink); }

.label { margin: 0 0 8px; font: 700 12px var(--font-body); letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); }
.addresses { margin: 0; padding: 0; list-style: none; border: 1px solid var(--line); border-radius: 16px; background: var(--surface); }
.address { display: flex; align-items: center; gap: 12px; padding: 12px 16px; flex-wrap: wrap; }
.address + .address { border-top: 1px solid var(--line); }
.addressLabel { width: 150px; font-size: 14px; color: var(--ink-2); }
.url { flex: 1; min-width: 0; font: 400 13.5px var(--font-mono); color: var(--ink); overflow-wrap: anywhere; }
.dim .url { color: var(--ink-3); }

.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.tile {
  display: flex; flex-direction: column; align-items: flex-start; gap: 6px; padding: 16px; border-radius: 16px;
  border: 1px solid var(--line); background: var(--surface); color: var(--ink); font: 700 15px var(--font-body);
  cursor: pointer; text-align: left;
}
.tile:hover { background: var(--surface-2); }
.tile small { font: 500 12.5px var(--font-body); color: var(--ink-3); }
.off { border-style: dashed; background: transparent; color: var(--ink-3); cursor: default; opacity: .8; }
.off:hover { background: transparent; }
.danger:hover { color: var(--paprika); border-color: var(--paprika-soft); }

.center { display: flex; flex-direction: column; align-items: center; gap: 16px; text-align: center; padding: 12px 0; }
.progress { width: min(460px, 100%); display: flex; flex-direction: column; gap: 8px; }
.caption { display: flex; justify-content: space-between; font-size: 14px; color: var(--ink-2); }
.footnote { margin: 0; font-size: 14px; color: var(--ink-3); max-width: 460px; }

.wrongHead { display: flex; flex-direction: column; align-items: flex-start; gap: 12px; }
.logs { margin: 0; max-height: 320px; overflow: auto; font: 400 12.5px/1.6 var(--font-mono); color: var(--ink-2); white-space: pre-wrap; }
.was { display: flex; align-items: center; gap: 12px; font-size: 14px; color: var(--ink-3); }
.was code { font: 400 13px var(--font-mono); }

.modalSub { margin: 0; font-size: 15px; line-height: 1.5; color: var(--ink-2); }
.modalFoot { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

.bin { margin: 0; padding: 0; list-style: none; border: 1px solid var(--line); border-radius: 16px; }
.binRow { display: flex; flex-wrap: wrap; gap: 4px 12px; padding: 12px 16px; }
.binRow + .binRow { border-top: 1px solid var(--line); }
.binLabel { flex: 1; font-size: 15px; color: var(--ink); }
.binDetail { font-size: 14px; color: var(--ink-3); }
.names { width: 100%; margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 6px; }
.names code { padding: 2px 8px; border-radius: 8px; background: var(--surface-2); font: 400 12.5px var(--font-mono); }
```

- [ ] **Step 2: Tiles and AddressRows**

`web/apps/console/src/screens/project/Tiles.tsx`:

```tsx
import { cx } from "@eggie/ui";
import { FOLDER, GLOBE, MAGNIFIER, TRASH } from "../icons";
import s from "./ProjectPage.module.css";

export function Tiles({ onAnalyze, onDelete }: { onAnalyze: () => void; onDelete: () => void }) {
  return (
    <div className={s.tiles}>
      <button type="button" className={s.tile} onClick={onAnalyze}>{MAGNIFIER}Analyze</button>
      <div className={cx(s.tile, s.off)} aria-disabled="true">{FOLDER}Files<small>Soon</small></div>
      <div className={cx(s.tile, s.off)} aria-disabled="true">{GLOBE}Public address<small>Needs an account</small></div>
      <button type="button" className={cx(s.tile, s.danger)} onClick={onDelete}>{TRASH}Delete</button>
    </div>
  );
}
```

`web/apps/console/src/screens/project/AddressRows.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Button, cx } from "@eggie/ui";
import { hostOf } from "../../projects/format";
import type { WebEntry } from "../../projects/types";
import s from "./ProjectPage.module.css";

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "refused">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const timer = window.setTimeout(() => setState("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [state]);
  const copy = () => navigator.clipboard.writeText(text).then(() => setState("copied"), () => setState("refused"));
  return (
    <Button variant="quiet" onClick={copy}>
      {state === "copied" ? "Copied" : state === "refused" ? "Couldn't copy" : "Copy"}
    </Button>
  );
}

export function AddressRows({ web, live }: { web: WebEntry[]; live: boolean }) {
  if (web.length === 0) return null;
  return (
    <section>
      <h2 className={s.label}>Where to find it</h2>
      <ul className={s.addresses}>
        {web.map((entry) => (
          <li key={entry.url} className={cx(s.address, !live && s.dim)}>
            <span className={s.addressLabel}>{entry.primary ? "The app itself" : entry.service}</span>
            <span className={s.url}>{hostOf(entry.url)}</span>
            {live && <CopyButton text={entry.url} />}
          </li>
        ))}
      </ul>
    </section>
  );
}
```

- [ ] **Step 3: StartingBody**

`web/apps/console/src/screens/project/StartingBody.tsx`:

```tsx
import { Egg, ProgressBar, StateBadge } from "@eggie/ui";
import { Elapsed } from "../../components/Elapsed";
import { phaseCaption } from "../../projects/copy";
import { useJob } from "../../projects/queries";
import type { ActiveJob, Project } from "../../projects/types";
import s from "./ProjectPage.module.css";

export function StartingBody({ project, job }: { project: Project; job: ActiveJob }) {
  const live = useJob(job.id);
  const caption = phaseCaption(live.data?.phase ?? job.phase, project.first_run);
  return (
    <div className={s.center}>
      <Egg size={88} bob />
      <StateBadge state="starting" />
      <h1 className={s.big}>Heating the pan for {project.id}</h1>
      <p className={s.lead}>
        {project.first_run
          ? "The first start always takes the longest — it's fetching everything your app needs to run. A few minutes, once. After that it's about ten seconds."
          : "This usually takes about ten seconds."}
      </p>
      <div className={s.progress}>
        <ProgressBar label={caption} />
        <div className={s.caption}>
          <span>{caption}</span>
          <span><Elapsed startedAt={job.started_at} /> so far</span>
        </div>
      </div>
      <p className={s.footnote}>
        You can wander off — this keeps going with the page closed, and the address turns on by itself when it's ready.
      </p>
    </div>
  );
}
```

- [ ] **Step 4: WrongBody**

`web/apps/console/src/screens/project/WrongBody.tsx`:

```tsx
import { useState } from "react";
import { Button, Collapsible, Egg, Modal, Notice, PromptCard, StateBadge } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { CAUSE_COPY, actionError, primaryUrl } from "../../projects/copy";
import { hostOf } from "../../projects/format";
import { fixPrompt } from "../../projects/prompts";
import { useLifecycle, useLogs } from "../../projects/queries";
import type { Project } from "../../projects/types";
import type { Cause } from "../../projects/view";
import s from "./ProjectPage.module.css";

function Logs({ id, wanted }: { id: string; wanted: boolean }) {
  const logs = useLogs(id, wanted);
  if (logs.data !== undefined) {
    return <pre className={s.logs}>{logs.data.trim() ? logs.data : "There aren't any logs to show yet."}</pre>;
  }
  if (logs.isError) {
    return (
      <p className={s.muted}>
        {logs.error instanceof ApiError && logs.error.code === "logs_unavailable"
          ? "There aren't any logs to show yet."
          : logs.error.message}
      </p>
    );
  }
  return <p className={s.muted}>Fetching the logs…</p>;
}

export function WrongBody({ project, cause, detail }: { project: Project; cause: Cause; detail: string | null }) {
  const lifecycle = useLifecycle(project.id);
  const [fixing, setFixing] = useState(false);
  const [wantLogs, setWantLogs] = useState(false);
  const copy = CAUSE_COPY[cause];
  const primary = primaryUrl(project);

  return (
    <>
      <header className={s.wrongHead}>
        <Egg tone="cold" size={56} />
        <StateBadge state="wrong" />
        <h1 className={s.big}>{copy.heading(project.id)}</h1>
      </header>
      <p className={s.lead}>{copy.body}</p>
      {detail && <p className={s.detail}>{detail}</p>}
      <div className={s.actions}>
        <Button variant="primary" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("restart")}>Try again</Button>
        <Button onClick={() => setFixing(true)}>Get a prompt that fixes it</Button>
        <span className={s.note}>Nothing is lost. Your files are exactly where you left them.</span>
      </div>
      {lifecycle.error && <Notice>{actionError(lifecycle.error)}</Notice>}
      <Collapsible
        boxed
        summary="The raw details"
        aside="for your coding agent, or for us"
        onToggle={(open) => open && setWantLogs(true)}
      >
        {wantLogs && <Logs id={project.id} wanted={wantLogs} />}
      </Collapsible>
      {primary && (
        <div className={s.was}>
          <span>Was going to be</span>
          <code>{hostOf(primary)}</code>
        </div>
      )}
      <Modal open={fixing} onClose={() => setFixing(false)} title="A prompt that fixes it">
        <PromptCard prompt={fixPrompt(cause, project.id, detail)} />
        <div className={s.modalFoot}>
          <Button onClick={() => setFixing(false)}>Close</Button>
        </div>
      </Modal>
    </>
  );
}
```

- [ ] **Step 5: AnalyzeModal**

`web/apps/console/src/screens/project/AnalyzeModal.tsx`:

```tsx
import { Button, Modal, PromptCard } from "@eggie/ui";
import { ANALYZE_PROMPT } from "../../projects/prompts";
import s from "./ProjectPage.module.css";

export function AnalyzeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="Ask for a once-over">
      <p className={s.modalSub}>
        Eggie doesn't read your code — your coding agent does. Here's the ask, written so you get a plain-language
        answer back.
      </p>
      <PromptCard
        prompt={ANALYZE_PROMPT}
        aside="for Claude Code, Codex, whoever's cooking"
        hint="Now paste it into your coding agent and press enter."
      />
      <div className={s.modalFoot}>
        <Button onClick={onClose}>Close</Button>
        <span className={s.note}>Nothing was sent anywhere. This is just words on your clipboard.</span>
      </div>
    </Modal>
  );
}
```

- [ ] **Step 6: ProjectPage**

`web/apps/console/src/screens/project/ProjectPage.tsx`:

```tsx
import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Button, Egg, Notice, PromptCard, StateBadge } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { Elapsed } from "../../components/Elapsed";
import { actionError, primaryUrl } from "../../projects/copy";
import { waitingPrompt } from "../../projects/prompts";
import { useDeleteProject, useLifecycle, useProject } from "../../projects/queries";
import { projectView } from "../../projects/view";
import { ARROW } from "../icons";
import { AddressRows } from "./AddressRows";
import { AnalyzeModal } from "./AnalyzeModal";
import { StartingBody } from "./StartingBody";
import { Tiles } from "./Tiles";
import { WrongBody } from "./WrongBody";
import s from "./ProjectPage.module.css";

export function ProjectPage() {
  const { id = "" } = useParams();
  const query = useProject(id);
  const lifecycle = useLifecycle(id);
  const forget = useDeleteProject(id);
  const navigate = useNavigate();
  const [modal, setModal] = useState<"analyze" | "delete" | null>(null);

  const back = <Link to="/" className={s.back}>‹ All projects</Link>;

  if (query.data === undefined) {
    if (query.error instanceof ApiError && query.error.code === "project_not_found") {
      return (
        <section className={s.page}>
          {back}
          <h1 className={s.name}>No project called {id}</h1>
        </section>
      );
    }
    return (
      <section className={s.page}>
        {back}
        {query.isError ? <p className={s.error}>{query.error.message}</p> : <p className={s.muted}>Checking the kitchen…</p>}
      </section>
    );
  }

  const project = query.data;
  const view = projectView(project);
  const primary = primaryUrl(project);
  const tiles = <Tiles onAnalyze={() => setModal("analyze")} onDelete={() => setModal("delete")} />;
  const failed = lifecycle.error && <Notice>{actionError(lifecycle.error)}</Notice>;
  const head = (sub?: string) => (
    <header className={s.head}>
      <Egg size={44} tone={view.badge === "running" || view.badge === "starting" ? "yolk" : "cold"} />
      <div className={s.grow}>
        <h1 className={s.name}>{project.id}</h1>
        {sub && <p className={s.sub}>{sub}</p>}
      </div>
      <StateBadge state={view.badge} />
    </header>
  );

  let body: ReactNode;
  switch (view.kind) {
    case "starting":
      body = <StartingBody project={project} job={view.job} />;
      break;
    case "stopping":
      body = (
        <>
          {head()}
          <p className={s.lead}>Putting {project.id} away… <Elapsed startedAt={view.job.started_at} /></p>
        </>
      );
      break;
    case "wrong":
      body = (
        <>
          <WrongBody project={project} cause={view.cause} detail={view.detail} />
          {tiles}
        </>
      );
      break;
    case "running": {
      const count = project.web.length;
      body = (
        <>
          {head(count > 0 ? `${count} ${count === 1 ? "address" : "addresses"} open` : undefined)}
          <div className={s.actions}>
            {primary && (
              <Button variant="primary" onClick={() => window.open(primary, "_blank", "noopener,noreferrer")}>
                Open in browser{ARROW}
              </Button>
            )}
            <Button disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("down")}>Stop</Button>
            <Button disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("restart")}>Restart</Button>
          </div>
          {failed}
          <AddressRows web={project.web} live />
          {tiles}
        </>
      );
      break;
    }
    case "stopped":
      body = (
        <>
          {head()}
          <div className={s.actions}>
            <Button variant="primary" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate("up")}>Start</Button>
          </div>
          {failed}
          <AddressRows web={project.web} live={false} />
          {tiles}
        </>
      );
      break;
    case "waiting":
      body = (
        <>
          {head("Nothing to cook yet")}
          <p className={s.lead}>
            {project.id} is an empty folder at ~/projects/{project.id}. Your coding agent fills it in; once there's a
            docker-compose.yml, Start appears here.
          </p>
          <PromptCard prompt={waitingPrompt(project.id)} />
          {tiles}
        </>
      );
      break;
    case "gone":
      body = (
        <>
          {head()}
          <h2 className={s.big}>The folder for {project.id} has gone missing</h2>
          <p className={s.lead}>Eggie still remembers it, but ~/projects/{project.id} isn't there any more.</p>
          <div className={s.actions}>
            <Button
              variant="danger"
              disabled={forget.isPending}
              onClick={() => forget.mutate(false, { onSuccess: (result) => navigate("/", { state: { deleted: result } }) })}
            >
              Forget it
            </Button>
          </div>
          {forget.error && <Notice>{actionError(forget.error)}</Notice>}
        </>
      );
      break;
  }

  return (
    <section className={s.page}>
      {back}
      {query.isError && <Notice>{query.error.message}</Notice>}
      {body}
      <AnalyzeModal open={modal === "analyze"} onClose={() => setModal(null)} />
      {/* delete modal: Task 9 */}
    </section>
  );
}
```

- [ ] **Step 7: Route**

In `App.tsx` import `ProjectPage` from `./screens/project/ProjectPage` and add inside `<Routes>` after the `/` route:

```tsx
                <Route path="/p/:id" element={<ProjectPage />} />
```

- [ ] **Step 8: Verify**

Run (from `web/`): `npm run typecheck && npm test && npm run build && npm run check-offline`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add -A web/apps/console/src
git commit -m "Give every project a page for each of its states, with prompts to fix and analyse it"
```

---

### Task 9: Delete a project

**Files:**
- Create: `web/apps/console/src/screens/project/DeleteModal.tsx`
- Modify: `web/apps/console/src/screens/project/ProjectPage.tsx` (the `{/* delete modal: Task 9 */}` line)

**Interfaces:**
- Consumes: `useDeletePreview`, `useDeleteProject`, `deleteRows`, `actionError`, styles `bin*`, `names`, `modalSub`, `modalFoot`, `note`, `muted` from Task 8's CSS.

No tests: `deleteRows` (Task 2) holds the logic.

- [ ] **Step 1: DeleteModal**

`web/apps/console/src/screens/project/DeleteModal.tsx`:

```tsx
import type { ReactNode } from "react";
import { useNavigate } from "react-router";
import { Button, Modal, Notice } from "@eggie/ui";
import { actionError } from "../../projects/copy";
import { deleteRows } from "../../projects/format";
import { useDeletePreview, useDeleteProject } from "../../projects/queries";
import s from "./ProjectPage.module.css";

export function DeleteModal({ id, open, onClose }: { id: string; open: boolean; onClose: () => void }) {
  const preview = useDeletePreview(id, open);
  const remove = useDeleteProject(id);
  const navigate = useNavigate();

  const close = () => {
    remove.reset();
    onClose();
  };

  let contents: ReactNode;
  if (preview.data) {
    const rows = deleteRows(preview.data);
    contents =
      rows.length === 0 ? (
        <p className={s.muted}>It's empty — only its name goes.</p>
      ) : (
        <ul className={s.bin}>
          {rows.map((row) => (
            <li key={row.label} className={s.binRow}>
              <span className={s.binLabel}>{row.label}</span>
              <span className={s.binDetail}>{row.detail}</span>
              {row.names && (
                <ul className={s.names}>
                  {row.names.map((name) => <li key={name}><code>{name}</code></li>)}
                </ul>
              )}
            </li>
          ))}
        </ul>
      );
  } else if (preview.isError) {
    contents = <Notice>{preview.error.message}</Notice>;
  } else {
    contents = <p className={s.muted}>Counting what's in there…</p>;
  }

  return (
    <Modal open={open} onClose={close} title={`Throw out ${id}?`}>
      <p className={s.modalSub}>This clears the whole counter. Here's exactly what goes in the bin:</p>
      {contents}
      <Notice>There's no undo and no bin to fish it out of. Anything you already downloaded stays yours; nothing else does.</Notice>
      {remove.error && <Notice>{actionError(remove.error)}</Notice>}
      <div className={s.modalFoot}>
        <Button
          variant="danger"
          disabled={remove.isPending || preview.isPending}
          onClick={() => remove.mutate(true, { onSuccess: (result) => navigate("/", { state: { deleted: result } }) })}
        >
          Yes, throw it out
        </Button>
        <Button onClick={close}>Keep it</Button>
        <span className={s.note}>Other projects aren't touched.</span>
      </div>
    </Modal>
  );
}
```

- [ ] **Step 2: Wire it**

In `ProjectPage.tsx` import `DeleteModal` and replace `{/* delete modal: Task 9 */}` with:

```tsx
      <DeleteModal id={project.id} open={modal === "delete"} onClose={() => setModal(null)} />
```

- [ ] **Step 3: Verify**

Run (from `web/`): `npm run typecheck && npm test && npm run build && npm run check-offline`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add web/apps/console/src/screens/project
git commit -m "Delete a project after showing exactly what goes with it"
```

---

### Task 10: Docs and full verification

**Files:**
- Modify: `CLAUDE.md` (the `npm run dev` line and the `web/` Layers bullet)

- [ ] **Step 1: CLAUDE.md**

Change the dev line to:

```
npm run dev          # Vite + an in-browser mock agent; ?scenario=empty|expired|handoff-spent|old-agent|down|lost-mid-use|wrong-host
```

In the `web/` bullet under Layers, after the sentence about `boot/boot.ts`, add:

```
  `projects/view.ts` maps the agent's `status`/`problem`/`job`/`empty` to one screen state for both the
  list and the project page; `projects/slugify.ts` mirrors the agent's `_slug`, held equal by
  `tests/fixtures/slugify-cases.json` (read by Vitest and `tests/agent/test_slug_cases.py`).
```

- [ ] **Step 2: Full verification**

Run (from `web/`): `npm test && npm run typecheck && npm run build && npm run check-offline`
Run (repo root): `TMPDIR=$PWD/.superpowers/tmp python3 -m pytest -q`
Expected: everything passes.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Document the project screens' state mapping and new dev scenarios"
```
