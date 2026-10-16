# GitHub picker: recognise repos that are already projects — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The console's "Pick a repository" list marks repos that already have a project ("Already a project → Open") and lets a repo whose project name is taken be imported under another name, instead of failing with `project 'x' already exists`.

**Architecture:** The API annotates each repo in `GET /github/repos` with `project` (the id of the project whose git `origin` is that repo), `name_taken` and `suggested_id`. The link is read from each project folder's `.git/config` on every request — nothing is stored, so a disconnect/reconnect of GitHub, a lost `state.db`, or a repo a coding agent cloned by hand all resolve the same way. The web picker renders three row kinds from those fields and shows an inline name field for a taken name (and for a 409 `project_exists` race).

**Tech Stack:** Python 3.12 / FastAPI (`runtime/eggie_api`), pytest; React 19 + TanStack Query + MSW, Vitest (`runtime/web`).

**Spec:** Issue eggie-io/eggie-resources#79 plus the design approved in-session (bounded path, no spec file):
- Link source = `remote.origin.url` in `<project dir>/.git/config`, GitHub URL forms normalised to lower-case `owner/name`; only project rows count (not unadopted folders).
- `GET /api/github/repos` repo items gain `project: str | null`, `name_taken: bool`, `suggested_id: str | null` (first free of `slug`, `slug-2`, `slug-3`, …; null when the repo name slugs to nothing).
- `POST /api/github/clone` unchanged (already takes `id`, already 409 `project_exists`).
- Linked row: "Already a project" + **Open** (closes modal, navigates to `/p/<id>`), no clone action. Name-taken row: "Name taken" badge, click opens inline name field prefilled with `suggested_id` + **Import**. A 409 `project_exists` from clone opens that same field with the error. Repos list refreshes after a clone lands.
- Out of scope: showing the repo on the project page.

## Global Constraints

- Python: `from __future__ import annotations`; follow the existing module style. `host/` must not be touched.
- Comments only for edge cases/workarounds; never mention issues/tickets in code comments.
- Tests: behaviour through the public surface (HTTP via `TestClient`, exported functions); no tests restating the framework. No network, no real git.
- Run Python tests with `TMPDIR=/home/ihor/tmp python3 -m pytest -q` (no `.venv` here; `/tmp/pytest-of-$USER` is root-owned).
- Web: `cd runtime/web && npm test` and `npm run typecheck` must both pass.
- Copy: "Already a project", "Open", "Name taken", "Import", "Cancel"; field label "Name the new project".

## Review Focus

1. A project folder without `.git`, with an unreadable/garbage `.git/config`, or with a non-GitHub origin — the repos list must still load (no 500), that project just isn't linked.
2. Case: GitHub returns `Octo/App` while the origin says `octo/app.git` — must still link.
3. The `origin` URL carries credentials (`https://x-access-token:…@github.com/o/n.git`) — must still link, and nothing about the URL is echoed back.
4. A taken name where `slug`, `slug-2` are both taken — suggestion is `slug-3`; a folder with no row also counts as taken.
5. Clone 409 after the list was fetched — the user gets the name field with the server's message, not a dead red line; Cancel clears the error.

---

### Task 1: API — mark repos that are already projects and suggest a free name

**Files:**
- Modify: `runtime/eggie_api/infra/github.py` (add `repo_from_url`, `origin_repo` next to `valid_repo`)
- Modify: `runtime/eggie_api/services/clone.py` (add `annotate`, `_taken`, `_free_id`; reuse `_taken` in `clone`)
- Modify: `runtime/eggie_api/rest/routers/github.py` (`github_repos` passes the list through `s.clone.annotate`)
- Test: `tests/runtime/api/test_github.py` (URL/config parsing), `tests/runtime/api/test_api_github.py` (HTTP surface)

**Interfaces:**
- Produces (HTTP): each item of `GET /github/repos` → `{"full_name", "private", "description", "updated_at", "project": str | None, "name_taken": bool, "suggested_id": str | None}`.
- Produces (Python): `infra.github.repo_from_url(url: str) -> str | None`, `infra.github.origin_repo(directory: Path) -> str | None`, `CloneService.annotate(repos: list[dict]) -> list[dict]`.

- [ ] **Step 1: Write the failing parsing tests** — append to `tests/runtime/api/test_github.py` (adjust the import line at the top to include `origin_repo, repo_from_url`):

```python
@pytest.mark.parametrize("url", [
    "https://github.com/Octo/App.git",
    "https://github.com/octo/app",
    "https://github.com/octo/app/",
    "https://x-access-token:secret@github.com/octo/app.git",
    "git@github.com:octo/app.git",
    "ssh://git@github.com/octo/app.git",
])
def test_github_remote_urls_resolve_to_lower_case_owner_slash_name(url):
    assert repo_from_url(url) == "octo/app"


@pytest.mark.parametrize("url", [
    "https://gitlab.com/octo/app.git",
    "https://github.com/octo",
    "https://github.com/octo/app/tree/main",
    "/srv/git/app.git",
    "",
])
def test_anything_but_a_github_repo_url_is_not_a_repo(url):
    assert repo_from_url(url) is None


def test_origin_is_read_from_the_origin_section_only(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        '[core]\n\tbare = false\n'
        '[remote "upstream"]\n\turl = https://github.com/someone/else.git\n'
        '[remote "origin"]\n\turl = https://github.com/Octo/App.git\n'
        '\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    assert origin_repo(tmp_path) == "octo/app"


def test_a_folder_without_a_readable_origin_has_none(tmp_path):
    assert origin_repo(tmp_path) is None
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_bytes(b"\xff\xfe[remote")
    assert origin_repo(tmp_path) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest -q tests/runtime/api/test_github.py`
Expected: collection ImportError on `origin_repo` / `repo_from_url`.

- [ ] **Step 3: Implement in `runtime/eggie_api/infra/github.py`** (after `valid_repo`):

```python
# Credentials in an https remote are skipped, not rejected: a hand-run clone
# may have baked a token into the URL.
_GITHUB_REMOTE = re.compile(
    r"(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([^/]+/[^/]+?)(?:\.git)?/?", re.IGNORECASE)


def repo_from_url(url: str) -> str | None:
    match = _GITHUB_REMOTE.fullmatch(url.strip())
    if not match or not valid_repo(match.group(1)):
        return None
    return match.group(1).lower()


def origin_repo(directory: Path) -> str | None:
    """The GitHub owner/name `directory`'s origin points at, lower-cased."""
    try:
        lines = (directory / ".git" / "config").read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    section = None
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == '[remote "origin"]':
            key, _, value = line.partition("=")
            if key.strip().lower() == "url":
                return repo_from_url(value.strip().strip('"'))
    return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest -q tests/runtime/api/test_github.py`
Expected: all PASS.

- [ ] **Step 5: Write the failing HTTP tests** — in `tests/runtime/api/test_api_github.py`. `REPO` there is `octo/app`. Add a helper and tests:

```python
def _project(app, env, pid, origin=None):
    folder = env.config.projects_root / pid
    folder.mkdir(parents=True)
    if origin is not None:
        (folder / ".git").mkdir()
        (folder / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {origin}\n')
    app.state.services.repos.projects.add(pid, str(folder), env.config.domain)


def test_a_repo_some_project_was_cloned_from_is_marked_with_that_project(env):
    client, _, app, _ = make(env, connected_github(
        repos=[([{**REPO, "full_name": "Octo/App"}], False)]))
    _project(app, env, "renamed", origin="https://github.com/octo/app.git")
    _project(app, env, "unrelated", origin="git@gitlab.com:octo/app.git")
    _project(app, env, "no-git")
    [repo] = client.get("/github/repos").json()["repos"]
    assert repo["project"] == "renamed"
    assert (repo["name_taken"], repo["suggested_id"]) == (False, "app")


def test_a_taken_name_suggests_the_first_free_suffix(env):
    client, _, app, _ = make(env, connected_github(repos=[([REPO], False)]))
    _project(app, env, "app")
    _project(app, env, "app-2")
    (env.config.projects_root / "app-3").mkdir()
    [repo] = client.get("/github/repos").json()["repos"]
    assert repo["project"] is None
    assert (repo["name_taken"], repo["suggested_id"]) == (True, "app-4")
```

Also update `test_repos_are_mapped_and_never_carry_the_token`'s expected item to include `"project": None, "name_taken": False, "suggested_id": "app"`.

- [ ] **Step 6: Run to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest -q tests/runtime/api/test_api_github.py -k "repos or taken or cloned_from"`
Expected: FAIL with `KeyError: 'project'` / dict mismatch.

- [ ] **Step 7: Implement in `runtime/eggie_api/services/clone.py`**

Import `origin_repo` alongside the other `..infra.github` names. Replace the existence check in `clone()`:

```python
        if self._taken(project_id):
            raise Conflict("project_exists",
                           f"project '{project_id}' already exists")
```

Add methods to `CloneService`:

```python
    def annotate(self, repos: list[dict]) -> list[dict]:
        linked: dict[str, str] = {}
        for row in self._projects.list():
            origin = origin_repo(self._loader.dir(row["id"]))
            if origin is not None:
                linked.setdefault(origin, row["id"])
        out = []
        for repo in repos:
            base = _slug(repo["full_name"].split("/")[1])
            out.append({**repo, "project": linked.get(repo["full_name"].lower()),
                        "name_taken": bool(base) and self._taken(base),
                        "suggested_id": self._free_id(base) if base else None})
        return out

    def _taken(self, project_id: str) -> bool:
        return (self._projects.get(project_id) is not None
                or self._loader.dir(project_id).exists())

    def _free_id(self, base: str) -> str:
        candidate, n = base, 1
        while self._taken(candidate):
            n += 1
            candidate = f"{base}-{n}"
        return candidate
```

Leave the in-job re-check (`directory.exists() or self._projects.get(...)`) as it is — it guards a race after the 202.

In `runtime/eggie_api/rest/routers/github.py`:

```python
    @router.get("/github/repos")
    def github_repos(page: int = 1) -> dict:
        listed = s.github_link.repos(page)
        return {**listed, "repos": s.clone.annotate(listed["repos"])}
```

- [ ] **Step 8: Run the API suite**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest -q tests/runtime/api`
Expected: all PASS (including the existing `test_clone_into_a_taken_name_is_refused`).

- [ ] **Step 9: Commit**

```bash
git add runtime/eggie_api tests/runtime/api
git commit -m "api: mark GitHub repos that already are projects and suggest a free name"
```

---

### Task 2: Console — "Already a project → Open" and import under another name

**Files:**
- Modify: `runtime/web/apps/console/src/github/github.ts` (Repo type, clone mutation takes `{repo, id?}`, `invalidateRepos`)
- Modify: `runtime/web/apps/console/src/github/view.ts` (add `repoRow`)
- Modify: `runtime/web/apps/console/src/github/view.test.ts`
- Modify: `runtime/web/apps/console/src/screens/github/RepoPicker.tsx`
- Modify: `runtime/web/apps/console/src/screens/github/GitHubModal.module.css` (`.rename`)
- Modify: `runtime/web/apps/console/src/mocks/handlers.ts` (annotate mock repos, clone takes `id`)

**Interfaces:**
- Consumes: Task 1's repo item fields `project`, `name_taken`, `suggested_id`; `POST /api/github/clone {repo, id?}` → 202 `{job_id, id}` or 409 `{error: {code: "project_exists", message}}`.
- Produces: `repoRow(repo: Repo): RepoRow` where `RepoRow = { kind: "linked"; projectId: string } | { kind: "nameTaken"; suggestion: string } | { kind: "free" }`; `invalidateRepos(client: QueryClient)`; `useCloneRepo()` mutation variable `{ repo: string; id?: string }`.

- [ ] **Step 1: Write the failing test** — append to `view.test.ts` (import `repoRow` from `./view` and `type Repo` from `./github`):

```ts
describe("repoRow", () => {
  const repo = (over: Partial<Repo>): Repo => ({
    full_name: "octo/app", private: false, description: null, updated_at: null,
    project: null, name_taken: false, suggested_id: "app", ...over,
  });

  it("opens the existing project even when its name is also taken", () => {
    expect(repoRow(repo({ project: "renamed", name_taken: true, suggested_id: "app-2" })))
      .toEqual({ kind: "linked", projectId: "renamed" });
  });

  it("asks for another name when the default one is taken", () => {
    expect(repoRow(repo({ name_taken: true, suggested_id: "app-2" })))
      .toEqual({ kind: "nameTaken", suggestion: "app-2" });
  });

  it("clones directly when the name is free or has no usable suggestion", () => {
    expect(repoRow(repo({}))).toEqual({ kind: "free" });
    expect(repoRow(repo({ name_taken: true, suggested_id: null }))).toEqual({ kind: "free" });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd runtime/web && npx vitest run apps/console/src/github/view.test.ts`
Expected: FAIL — `repoRow` is not exported.

- [ ] **Step 3: Implement `github.ts` and `view.ts`**

`github.ts`:

```ts
export type Repo = {
  full_name: string;
  private: boolean;
  description: string | null;
  updated_at: number | null;
  project: string | null;
  name_taken: boolean;
  suggested_id: string | null;
};
```

Add next to `invalidateGitHub`:

```ts
// The list marks repos that already are projects, so a landed clone changes it.
export function invalidateRepos(client: QueryClient) {
  return client.invalidateQueries({ queryKey: [...GITHUB, "repos"] });
}
```

Change `useCloneRepo`'s `mutationFn`:

```ts
    mutationFn: ({ repo, id }: { repo: string; id?: string }) =>
      api.post<{ job_id: string; id: string }>("/api/github/clone", id ? { repo, id } : { repo }),
```

`view.ts` (add `Repo` to the type import from `./github`):

```ts
export type RepoRow =
  | { kind: "linked"; projectId: string }
  | { kind: "nameTaken"; suggestion: string }
  | { kind: "free" };

export function repoRow(repo: Repo): RepoRow {
  if (repo.project) return { kind: "linked", projectId: repo.project };
  if (repo.name_taken && repo.suggested_id) return { kind: "nameTaken", suggestion: repo.suggested_id };
  return { kind: "free" };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd runtime/web && npx vitest run apps/console/src/github/view.test.ts`
Expected: PASS.

- [ ] **Step 5: Rewrite `RepoPicker`** (keep `Cloning` as is except the one addition below):

```tsx
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button, TextField } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { invalidateGitHub, invalidateRepos, useCloneRepo, useRepos, type Repo } from "../../github/github";
import { repoRow } from "../../github/view";
import { relativeTime } from "../../projects/format";
import { useJob } from "../../projects/queries";
import { slugify } from "../../projects/slugify";
import s from "./GitHubModal.module.css";

type Naming = { repo: string; id: string; error: string | null };

export function RepoPicker({ login, onDone }: { login: string; onDone: () => void }) {
  const repos = useRepos(true);
  const clone = useCloneRepo();
  const navigate = useNavigate();
  const [started, setStarted] = useState<{ job_id: string; id: string } | null>(null);
  const [naming, setNaming] = useState<Naming | null>(null);

  if (started) {
    return (
      <Cloning
        jobId={started.job_id}
        id={started.id}
        onDone={onDone}
        onRetry={() => setStarted(null)}
      />
    );
  }

  const start = (repo: Repo, id?: string) =>
    clone.mutate(
      { repo: repo.full_name, id },
      {
        onSuccess: setStarted,
        onError: (error) => {
          // Another project took the name after this list was fetched.
          if (error instanceof ApiError && error.code === "project_exists") {
            setNaming({ repo: repo.full_name, id: id ?? repo.suggested_id ?? "", error: error.message });
          }
        },
      },
    );
  const open = (projectId: string) => {
    onDone();
    navigate(`/p/${encodeURIComponent(projectId)}`);
  };
  const cancelNaming = () => {
    setNaming(null);
    clone.reset();
  };

  const all = repos.data?.pages.flatMap((page) => page.repos) ?? [];
  return (
    <>
      <p>Connected as @{login}. Pick a repository to make it a project:</p>
      {repos.isError && <p className={s.error}>{repos.error.message}</p>}
      {clone.error && !naming && <p className={s.error}>{clone.error.message}</p>}
      <ul className={s.repos}>
        {all.map((repo) => {
          const row = repoRow(repo);
          const details = (
            <>
              <span className={s.repoName}>{repo.full_name}</span>
              {repo.private && <span className={s.badge}>Private</span>}
              {row.kind === "linked" && <span className={s.badge}>Already a project</span>}
              {row.kind === "nameTaken" && <span className={s.badge}>Name taken</span>}
              {repo.updated_at !== null && (
                <span className={s.muted}>updated {relativeTime(repo.updated_at, Date.now())}</span>
              )}
            </>
          );
          return (
            <li key={repo.full_name}>
              {row.kind === "linked" ? (
                <div className={s.repo}>
                  {details}
                  <Button variant="quiet" onClick={() => open(row.projectId)}>Open</Button>
                </div>
              ) : (
                <button
                  className={s.repo}
                  disabled={clone.isPending}
                  onClick={() =>
                    row.kind === "nameTaken"
                      ? setNaming({ repo: repo.full_name, id: row.suggestion, error: null })
                      : start(repo)
                  }
                >
                  {details}
                </button>
              )}
              {naming?.repo === repo.full_name && (
                <form
                  className={s.rename}
                  onSubmit={(event) => {
                    event.preventDefault();
                    start(repo, naming.id);
                  }}
                >
                  <TextField
                    label="Name the new project"
                    value={naming.id}
                    onChange={(id) => setNaming({ ...naming, id, error: null })}
                    autoFocus
                    error={naming.error ?? undefined}
                    hint={slugify(naming.id) ? <>It'll be called <strong>{slugify(naming.id)}</strong></> : undefined}
                  />
                  <div className={s.actions}>
                    <Button variant="primary" type="submit" disabled={clone.isPending || !slugify(naming.id)}>
                      Import
                    </Button>
                    <Button variant="quiet" onClick={cancelNaming}>Cancel</Button>
                  </div>
                </form>
              )}
            </li>
          );
        })}
      </ul>
      {repos.isLoading && <p className={s.muted}>Fetching your repositories…</p>}
      {repos.hasNextPage && (
        <Button variant="quiet" onClick={() => void repos.fetchNextPage()} disabled={repos.isFetchingNextPage}>
          Load more
        </Button>
      )}
    </>
  );
}
```

In `Cloning`, inside the `if (cloned && !fired.current)` block, add `void invalidateRepos(client);` before `onDone();`.

Add to `GitHubModal.module.css` (the linked row is a `div.repo`, not a button, so it must not look clickable):

```css
div.repo { cursor: default; }
.rename { display: grid; gap: 8px; padding: 8px 10px 4px; }
```

- [ ] **Step 6: Update mocks** in `mocks/handlers.ts` so `npm run dev` shows all three row kinds:
  - Near the other module-level mock state, add `const linkedRepos = new Map<string, string>([["ada/recipe-box", "recipe-box"]]);`.
  - Repos handler: page 1 names become `["ada/recipe-box", "ada/garden-log", "kitchen-co/tiny-crm"]` (recipe-box and tiny-crm exist as mock projects in the default scenario). Each item gains:

```ts
const base = slugify(full_name.split("/")[1] ?? "");
let suggested = base;
for (let n = 2; projects.has(suggested); n++) suggested = `${base}-${n}`;
const linked = linkedRepos.get(full_name);
return {
  full_name, private: i % 2 === 0, description: null, updated_at: nowSec() - (i + page) * 86400,
  project: linked && projects.has(linked) ? linked : null,
  name_taken: projects.has(base),
  suggested_id: base ? suggested : null,
};
```
  - Clone handler: read `{ repo, id }`, call `makeProject(id ?? repo.split("/")[1] ?? repo)`, and on success `linkedRepos.set(repo, created.id)`.

- [ ] **Step 7: Verify**

Run: `cd runtime/web && npm test && npm run typecheck`
Expected: all PASS, no type errors.

Optional manual check (Playwright + google-chrome are available): `npm run dev`, open the console with the default scenario, open GitHub → the picker shows `ada/recipe-box` with "Already a project" + Open, `kitchen-co/tiny-crm` with "Name taken" → field prefilled `tiny-crm-2`.

- [ ] **Step 8: Commit**

```bash
git add runtime/web/apps/console/src
git commit -m "console: open repos that already are projects, rename on a taken name"
```
