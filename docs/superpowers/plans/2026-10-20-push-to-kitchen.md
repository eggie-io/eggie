# Push to Kitchen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push a local project (files, optionally data, secret names) from the local console to a
kitchen the user owns, and have the kitchen import it safely and wait for missing secrets.

**Architecture:** The local API builds a tar bundle in a root helper container, mints a ticket from
the service, opens a transfer on the kitchen, uploads the bundle, then follows the kitchen's
import. The kitchen's new `/api/transfers` routes have their own auth (ticket, then a push token),
replace the project from the grant's identity, merge secret names, and call `complete`. All service
and kitchen calls run against fakes.

**Tech Stack:** Python 3.12, FastAPI, sqlite, `tarfile`, `urllib`; React + TypeScript +
TanStack Query + MSW + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-20-push-to-kitchen-design.md`. Service contract:
`../eggie-web/docs/superpowers/specs/2026-10-20-kitchen-push-contract.md`.

## Global Constraints

- Python tests: `TMPDIR=/home/ihor/tmp python3 -m pytest <path> -q` (no `.venv` here; system
  `python3` is 3.12).
- Console tests: `cd runtime/web && npx vitest run <file>`; also `npm run typecheck`.
- Layering: `rest → services → domain | infra` (`tests/runtime/api/test_layering.py`). Routers hold
  no `try/except` and no business branching.
- No test touches the network, a VM or real Docker. Fakes are injected through `wiring.build(...)`.
- Secret values never go into the bundle, a log line, a URL or a job result.
- The ticket and the push token travel only in headers: `Eggie-Transfer-Ticket`,
  `Eggie-Transfer-Token`. They never appear in a URL or a log line.
- The kitchen takes project identity from the grant only, never from the bundle.
- Excluded from the files: dirs `.git`, `node_modules`, `.venv`, `__pycache__` (any path
  component) and the file `.eggie/overlay.yml`.
- Comments follow the user's rules: only for edge cases and workarounds, two lines at most, no
  ticket or doc references.
- Docs stay short (bullets, no long explanations); new release-testing cases get the next free ID,
  existing ones are never renumbered.
- Commit after each task on `feature/90-push-to-kitchen`, ending the message with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A compose file with a top-level `name:`, or a volume with its own `name:`. The docker volume
   names differ from `<id>_<key>`, and packing must mount the real ones (Task 1 test
   `test_docker_volume_name_honours_compose_and_volume_names`).
2. A symlink in the project pointing outside it, e.g. `secrets -> /etc`. Packing stores the link and
   never what it points to; the kitchen's `extract_archive` refuses it if it escapes (Task 5
   `test_symlinks_are_stored_as_links_not_followed`).
3. The grant's project name equals an existing non-pushed kitchen project. The kitchen picks
   `<slug>-2` and never touches the existing one (Task 9 `test_name_taken_by_a_kitchen_project_gets_a_suffix`).
4. The upload connection drops halfway. The kitchen deletes the partial file, releases the project
   lock and marks the push failed, so the next push isn't `kitchen_busy` (Task 9
   `test_failed_upload_releases_the_lock`).
5. The account signs out, or the device is revoked, while a push runs. The job fails with
   `not_signed_in`, not a traceback (Task 8 `test_sign_out_mid_push_fails_with_not_signed_in`).

---

### Task 1: Push rules (pure domain)

**Files:**
- Create: `runtime/eggie_api/domain/push.py`
- Test: `tests/runtime/api/test_push_domain.py`

**Interfaces:**
- Produces (all in `eggie_api.domain.push`):
  - `EXCLUDED_DIRS: frozenset[str]`, `EXCLUDED_FILES: frozenset[str]`, `is_excluded(rel: str) -> bool`
  - `DATABASE_IMAGES = frozenset({"postgres","postgis","mysql","mariadb","mongo","redis","valkey","elasticsearch","opensearch","clickhouse-server","couchdb","influxdb","neo4j"})`
  - `@dataclass(frozen=True) class Database: service: str; image: str; floating: bool`
  - `is_floating(image: str) -> bool`, `databases(compose: dict) -> list[Database]`
  - `@dataclass(frozen=True) class DataPlan: volumes: tuple[str, ...]; data_paths: tuple[str, ...]; external: tuple[str, ...]`
  - `data_plan(compose: dict) -> DataPlan`
  - `docker_volume_name(compose: dict, compose_name: str, key: str) -> str`
  - `REPLACE_NONE = "none"`, `REPLACE_FILES = "files"`, `REPLACE_ALL = "files-and-data"`
  - `replace_mode(pushed_before: bool, data: bool) -> str`
  - `check_replace(exists: bool, data: bool, replace: str) -> str | None`. Returns an error code or `None`.
  - `DEFAULT_HINT = "Set on your computer. Enter its value for the kitchen."`
  - `@dataclass(frozen=True) class SecretMerge: request: dict[str, str]; keep: tuple[str, ...]; unused: tuple[str, ...]; drop_requests: tuple[str, ...]`
  - `merge_secret_names(pushed: dict[str, str], stored: set[str], requested: set[str]) -> SecretMerge`
  - `kitchen_project_id(name: str, taken: set[str]) -> str`
  - `safe_return_path(path: str | None) -> str | None`
  - `build_manifest(*, data: bool, plan: DataPlan, secrets: dict[str, str]) -> dict`
  - `@dataclass(frozen=True) class Manifest: data: bool; volumes: tuple[str, ...]; data_paths: tuple[str, ...]; secrets: dict[str, str]`
  - `parse_manifest(raw: object, compose: dict) -> Manifest`, which raises `errors.Invalid("bad_manifest", …)`

- [ ] **Step 1: Write the failing tests**

```python
def test_is_excluded_matches_any_component_and_the_overlay():
    assert is_excluded("app/node_modules/x.js") and is_excluded(".git/config")
    assert is_excluded(".eggie/overlay.yml") and not is_excluded(".eggie/project.yml")
    assert not is_excluded("src/gitignore.txt")

def test_floating_tags():
    assert is_floating("postgres") and is_floating("postgres:latest") and is_floating("redis:alpine")
    assert not is_floating("postgres:16") and not is_floating("postgres@sha256:" + "a" * 64)
    assert not is_floating("registry.local:5000/postgres:16.2")

def test_databases_found_by_last_path_segment():
    compose = {"services": {"db": {"image": "postgis/postgis:16-3.4"},
                            "cache": {"image": "docker.io/library/redis"},
                            "web": {"image": "nginx"}, "app": {"build": "."}}}
    assert databases(compose) == [Database("cache", "docker.io/library/redis", True),
                                  Database("db", "postgis/postgis:16-3.4", False)]

def test_data_plan_named_volumes_external_skipped_and_db_bind_paths_only():
    compose = {"services": {
        "db": {"image": "postgres:16", "volumes": ["./pgdata:/var/lib/postgresql/data", "dbv:/x"]},
        "web": {"image": "node", "volumes": ["./src:/app", {"type": "volume", "source": "uploads", "target": "/u"}]},
        "ext": {"image": "x", "volumes": ["shared:/s"]}},
        "volumes": {"dbv": {}, "uploads": None, "shared": {"external": True}, "unused": {}}}
    assert data_plan(compose) == DataPlan(volumes=("dbv", "uploads"), data_paths=("pgdata",), external=("shared",))

def test_db_bind_mount_outside_the_project_is_not_data():
    compose = {"services": {"db": {"image": "mysql:8", "volumes": ["/srv/mysql:/var/lib/mysql", "../up:/u"]}}}
    assert data_plan(compose).data_paths == ()

def test_docker_volume_name_honours_compose_and_volume_names():
    compose = {"name": "shop", "volumes": {"dbv": {}, "pinned": {"name": "real-name"}}}
    assert docker_volume_name(compose, "shop", "dbv") == "shop_dbv"
    assert docker_volume_name(compose, "shop", "pinned") == "real-name"

def test_replace_rules():
    assert replace_mode(False, True) == "none" and replace_mode(True, False) == "files"
    assert replace_mode(True, True) == "files-and-data"
    assert check_replace(False, True, "none") is None
    assert check_replace(True, False, "none") == "already_on_kitchen"
    assert check_replace(True, True, "files") == "confirm_replace_data"
    assert check_replace(True, True, "files-and-data") is None

def test_secret_merge_table():
    m = merge_secret_names({"A": "", "B": "from local", "C": ""}, stored={"A", "OLD"}, requested={"C", "GONE"})
    assert m.keep == ("A",) and m.unused == ("OLD",) and m.drop_requests == ("GONE",)
    assert m.request == {"B": "from local", "C": DEFAULT_HINT}

def test_kitchen_project_id_suffixes_taken_ids():
    assert kitchen_project_id("My Shop", set()) == "my-shop"
    assert kitchen_project_id("My Shop", {"my-shop", "my-shop-2"}) == "my-shop-3"

@pytest.mark.parametrize("bad", ["", "x", "//evil.com", "/\\evil", "/a\\b", "/a#b", "/a b", "/a\x01", "/" + "a" * 1024])
def test_unsafe_return_paths_become_none(bad):
    assert safe_return_path(bad) is None

def test_safe_return_path_keeps_a_query():
    assert safe_return_path("/p/shop/secrets?x=1") == "/p/shop/secrets?x=1"

def test_manifest_round_trip_and_refusals():
    compose = {"services": {"db": {"image": "postgres:16", "volumes": ["./pgdata:/d", "dbv:/x"]}}, "volumes": {"dbv": {}}}
    raw = build_manifest(data=True, plan=data_plan(compose), secrets={"API_KEY": "hint"})
    assert parse_manifest(raw, compose) == Manifest(True, ("dbv",), ("pgdata",), {"API_KEY": "hint"})
    for broken in ({**raw, "format": 2}, {**raw, "volumes": ["nope"]}, {**raw, "data_paths": ["../etc"]},
                   {**raw, "secrets": [{"name": "PATH", "hint": ""}]}):
        with pytest.raises(Invalid):
            parse_manifest(broken, compose)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_push_domain.py -q`
Expected: FAIL (`ModuleNotFoundError: eggie_api.domain.push`).

- [ ] **Step 3: Implement `domain/push.py`**

- Image parsing: strip any `@digest` (pinned). The tag is the part after the last `:` only if
  that part contains no `/`, so a registry port isn't a tag. No tag means floating; a tag without
  a digit is floating too.
- Volume entries: the short syntax `src:dst[:mode]` is a bind when `src` starts with `.` or `/`;
  the long syntax uses `type`.
- A data path is `posixpath.normpath(src)` and must not start with `..` or `/`.
- Volume name: `compose["volumes"][key]["name"]` when set, else `f"{compose_name}_{key}"`.
- `merge_secret_names`: an empty hint becomes `DEFAULT_HINT`, and every output tuple is sorted.
- `safe_return_path`: the contract's rules (starts with `/`, second character not `/` or `\`, no
  `\`, `#`, whitespace or control characters, at most 1024 characters).
- `parse_manifest`: `format == 1`, each name through `domain.secrets.check_name`, and `volumes` /
  `data_paths` must be subsets of `data_plan(compose)`.
- `host/client.py` keeps its own copy of `EXCLUDED_DIRS`: `host/` must not import the runtime.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_push_domain.py tests/runtime/api/test_layering.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** (`Push rules: data plan, tags, secret merge, return path`)

---

### Task 2: Schema v7 and repos

**Files:**
- Modify: `runtime/eggie_api/infra/migrate.py` (append `_v7_push`)
- Create: `runtime/eggie_api/infra/repos/kitchen_pushes.py`, `runtime/eggie_api/infra/repos/pushed_projects.py`
- Modify: `runtime/eggie_api/infra/repos/__init__.py`, `infra/repos/secrets.py`, `infra/repos/projects.py`
- Test: `tests/runtime/api/test_repos.py`, `tests/runtime/api/test_migrate.py`

**Interfaces:**
- `_v7_push` adds:
  - table `kitchen_pushes(local_id TEXT, instance_id TEXT, console_hostname TEXT NOT NULL, kitchen_project TEXT, pushed_at REAL NOT NULL, urls_json TEXT NOT NULL DEFAULT '[]', secrets_missing INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(local_id, instance_id))`;
  - table `pushed_projects(local_id TEXT PRIMARY KEY, cloud_project_id TEXT NOT NULL UNIQUE, source_device_id TEXT, first_pushed_at REAL NOT NULL, last_pushed_at REAL)`;
  - column `secrets.unused_at REAL`;
  - column `projects.start_when_ready INTEGER NOT NULL DEFAULT 0`.
- `KitchenPushRepo(db)`:
  - `record(local_id, instance_id, *, console_hostname: str, kitchen_project: str | None, urls: list[str], secrets_missing: int, at: float) -> None` (upsert);
  - `for_project(local_id) -> dict[str, dict]`, keyed by instance id; values are `{pushed_at, console_hostname, kitchen_project, urls, secrets_missing}`;
  - `pushed_ids() -> set[str]`.
- `PushedProjectRepo(db)`:
  - `by_cloud_id(cloud_id) -> dict | None`;
  - `local_ids() -> set[str]`;
  - `claim(local_id, cloud_project_id, source_device_id, at) -> None` (insert; an existing row keeps `first_pushed_at`);
  - `touch(local_id, at) -> None` (sets `last_pushed_at`);
  - `remove(local_id) -> None`.
- `SecretRepo.mark_unused(project_id, names, at) -> None`, `SecretRepo.clear_unused(project_id, names) -> None`. `names()` now returns `{name, updated_at, unused_at}`.
- `ProjectRepo.set_start_when_ready(id, on: bool) -> None`. `get()` and `list()` rows carry `start_when_ready`.
- `Repos` gains the fields `kitchen_pushes` and `pushed_projects`.

- [ ] **Step 1: Write the failing tests** in `test_repos.py`

```python
def test_kitchen_push_record_upserts_and_lists_by_instance(repos):
    repos.kitchen_pushes.record("shop", "k1", console_hostname="a.eggie.io", kitchen_project=None, urls=[], secrets_missing=0, at=1.0)
    repos.kitchen_pushes.record("shop", "k1", console_hostname="a.eggie.io", kitchen_project="shop", urls=["https://u"], secrets_missing=2, at=2.0)
    assert repos.kitchen_pushes.for_project("shop") == {"k1": {"pushed_at": 2.0, "console_hostname": "a.eggie.io",
        "kitchen_project": "shop", "urls": ["https://u"], "secrets_missing": 2}}
    assert repos.kitchen_pushes.pushed_ids() == {"shop"}

def test_pushed_project_claim_keeps_first_pushed_at(repos):
    repos.pushed_projects.claim("shop", "c-1", "dev-1", 1.0)
    repos.pushed_projects.claim("shop", "c-1", "dev-1", 5.0)
    assert repos.pushed_projects.by_cloud_id("c-1")["first_pushed_at"] == 1.0

def test_unused_mark_is_set_and_cleared(repos):
    repos.projects.add("shop", "/p/shop", "d")
    repos.secrets.set("shop", {"A": "1"})
    repos.secrets.mark_unused("shop", ["A"], 3.0)
    assert repos.secrets.names("shop")[0]["unused_at"] == 3.0
    repos.secrets.clear_unused("shop", ["A"])
    assert repos.secrets.names("shop")[0]["unused_at"] is None
```

Plus in `test_migrate.py`: a v6 database migrates to v7, and `projects.start_when_ready` reads 0.
(Use the file's existing pattern and its `repos` fixture, or open `Repos.open(Database(tmp_path/"s.db"))`.)

- [ ] **Step 2: Run them and confirm they fail.** Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_repos.py tests/runtime/api/test_migrate.py -q`. Expected: FAIL (missing attributes).

- [ ] **Step 3: Implement the migration and the repos.** Use `_add_column` for the two columns.

- [ ] **Step 4: Run the tests and confirm they pass** (same command, plus `tests/runtime/api -q -x` to catch callers of `names()`).

- [ ] **Step 5: Commit** (`Schema v7: push records, pushed projects, unused secrets, start-when-ready`)

---

### Task 3: Service client for transfers

**Files:**
- Modify: `runtime/eggie_api/infra/cloud.py`, `tests/runtime/api/fake_cloud.py`
- Test: `tests/runtime/api/test_cloud.py`

**Interfaces:**
- `CloudError(code, message, status, retry_after: int | None = None)`. `_error_from(status, raw, headers=None)` reads `Retry-After` (integer seconds; anything else gives `None`).
- `Cloud.transfer_targets(token) -> dict`, which is `GET /v1/transfers/targets`.
- `Cloud.mint_transfer_ticket(token, instance_id, project_id) -> dict`, which is `POST /v1/transfers/tickets` with body `{"instance_id","project_id"}`.
- `Cloud.redeem_transfer_ticket(token, ticket) -> dict`, which is `POST /v1/transfers/tickets/redeem` with body `{"ticket"}`.
- `Cloud.complete_transfer(token, ticket_id) -> None`, which is `POST /v1/transfers/{quoted id}/complete` with no body.
- `FakeCloud` gains the same four methods via `_next`.

- [ ] **Step 1: Write the failing tests.** Use the opener fake already in `test_cloud.py`:
  - `test_rate_limit_error_carries_retry_after` asserts that a 429 with `Retry-After: 120` and the envelope raises `CloudError` with `.code == "transfer_rate_limited"` and `.retry_after == 120`;
  - `test_mint_sends_instance_and_project`, `test_redeem_sends_ticket_in_the_body_not_the_url` and `test_complete_posts_no_body` assert the path and method, the JSON body, and that the ticket string is not in `request.full_url`.
- [ ] **Step 2: Run them and confirm they fail.** Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_cloud.py -q`.
- [ ] **Step 3: Implement.** Pass `e.headers` from the `HTTPError` into `_error_from`.
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Commit** (`Service client: transfer endpoints and Retry-After`)

---

### Task 4: Sync keeps pushed records; the kitchen skips pushed projects

**Files:**
- Modify: `runtime/eggie_api/domain/sync.py`, `runtime/eggie_api/services/sync.py`
- Test: `tests/runtime/api/test_sync.py`

**Interfaces:**
- `plan(local_ids, mapping, org_id, keep: frozenset[str] = frozenset()) -> list`. A mapped id that is missing locally and is in `keep` becomes `Forget(local_id)` instead of `Delete`.
- `run_pass` computes `keep = frozenset(repos.kitchen_pushes.pushed_ids())`, and drops `repos.pushed_projects.local_ids()` from `local_ids`.

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_forgets_instead_of_deleting_a_pushed_project():
    assert plan(set(), {"shop": M("c-shop")}, "org-1", keep=frozenset({"shop"})) == [Forget("shop")]

def test_a_pass_keeps_the_service_record_of_a_pushed_project(tmp_path):
    cloud = FakeCloud()
    repos, account = setup(tmp_path, cloud)
    repos.cloud_projects.map("shop", "c-shop", "org-1")
    repos.kitchen_pushes.record("shop", "k1", console_hostname="h", kitchen_project="shop", urls=[], secrets_missing=0, at=1.0)
    run_pass(account, cloud, repos)
    assert cloud.calls == [] and repos.cloud_projects.mapping() == {}

def test_a_kitchen_never_registers_a_pushed_project(tmp_path):
    cloud = FakeCloud()
    repos, account = setup(tmp_path, cloud, ["shop"])
    repos.pushed_projects.claim("shop", "c-shop", "dev-1", 1.0)
    run_pass(account, cloud, repos)
    assert "create_project" not in cloud.names()
```

- [ ] **Step 2: Run them and confirm they fail.** Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_sync.py -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Commit** (`Sync: keep a pushed project's record, skip pushed copies on a kitchen`)

---

### Task 5: Bundle packing and volume restore (root helper)

**Files:**
- Create: `runtime/eggie_api/infra/bundle.py`
- Modify: `runtime/eggie_api/infra/docker.py`: extract `own_image(runner) -> str | None` from `remove_tree_as_root` and reuse it there.
- Test: `tests/runtime/api/test_bundle.py`

**Interfaces:**
- `write_bundle(out: Path, project_dir: Path, manifest: dict, *, volumes: dict[str, Path], data_paths: Sequence[str], include_data: bool) -> int`. It writes the bundle and returns its size in bytes. Layout: `manifest.json`, then `files/<rel>` for every non-excluded path. Data paths are included only when `include_data`. With `include_data`, it adds `volumes/<key>/<rel>` for each entry of `volumes`. It never follows symlinks (`tar.add(..., recursive=False)` per entry, with the walk using `followlinks=False`).
- `restore_volume(source: Path, target: Path) -> None`. It empties `target`, keeping the directory itself, then copies `source` into it, preserving modes, ownership and symlinks (`shutil.copytree(..., symlinks=True, dirs_exist_ok=True)` plus `os.chown` per entry, since it runs as root).
- `main(argv: list[str]) -> int` for `python -m eggie_api.infra.bundle pack --out O --project P --manifest M [--volume key=/path ...] [--data]` and `restore --source S --target T`.
- `pack_argv(image, *, out_dir, project_dir, volumes: dict[str, str], out_name, manifest_path, include_data) -> list[str]`, where `volumes` maps a key to the docker volume name. It returns `[DOCKER, "run", "--rm", "--user", "0", "--entrypoint", "python", "-v", f"{project_dir}:/src:ro", "-v", f"{out_dir}:/out", *("-v", f"{name}:/vol/{key}:ro" per volume), image, "-m", "eggie_api.infra.bundle", "pack", ...]`.
- `restore_argv(image, *, source_dir, volume_name) -> list[str]`, with the same shape: the source is mounted at `/src:ro` and the volume at `/vol`.

- [ ] **Step 1: Write the failing tests** (on real temp dirs; no Docker)

```python
def test_bundle_has_manifest_files_and_no_excluded_paths(tmp_path): ...
    # project with src/app.py, node_modules/x, .git/config, .eggie/overlay.yml, .eggie/project.yml, .env
    # names == {"manifest.json", "files/src/app.py", "files/.eggie/project.yml", "files/.env", ...dirs}
def test_data_paths_only_with_include_data(tmp_path): ...
    # pgdata/base/1 present only when include_data=True; volumes/dbv/f present only then too
def test_symlinks_are_stored_as_links_not_followed(tmp_path): ...
    # link "out" -> tmp_path/"elsewhere"/"secret.txt": member is SYMTYPE, no member contains the secret bytes
def test_manifest_secrets_carry_only_name_and_hint(tmp_path): ...
    # manifest.json read back from the bundle: every secrets entry has exactly the keys {"name", "hint"}
def test_restore_volume_replaces_contents(tmp_path): ...
    # target with old.txt; source with new.txt -> target has only new.txt
def test_pack_argv_mounts_project_read_only_and_runs_the_module():
    argv = pack_argv("img", out_dir="/opt/eggie/transfers/out", project_dir="/opt/eggie/projects/shop",
                     volumes={"dbv": "shop_dbv"}, out_name="j1.tar", manifest_path="/out/j1.json", include_data=True)
    assert "/opt/eggie/projects/shop:/src:ro" in argv and "shop_dbv:/vol/dbv:ro" in argv
    assert argv[argv.index("img") + 1:argv.index("img") + 4] == ["-m", "eggie_api.infra.bundle", "pack"]
```

(Fill in each `...` body with the stated assertions; open the result with `tarfile.open(out)`.)

- [ ] **Step 2: Run them and confirm they fail.** Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_bundle.py -q`.
- [ ] **Step 3: Implement `infra/bundle.py`.** It imports `domain.push.is_excluded`; infra already imports domain in `docker.py`.
- [ ] **Step 4: Run the tests and confirm they pass,** along with `test_api_projects_ui.py` (`remove_tree_as_root` still works).
- [ ] **Step 5: Commit** (`Bundle: pack a project and its data as root, restore a volume`)

---

### Task 6: Secret names from a push, unused marks, start-when-ready

**Files:**
- Modify: `runtime/eggie_api/services/secrets.py`, `services/lifecycle.py`, `services/projects.py`, `rest/routers/lifecycle.py`, `wiring.py`
- Test: `tests/runtime/api/test_api_secrets.py`, `tests/runtime/api/test_lifecycle.py`

**Interfaces:**
- `SecretService.apply_pushed(project_id, pushed: dict[str, str]) -> int` applies `merge_secret_names`. It creates requests, deletes the dropped requests, marks the `unused` names, clears the mark on `keep` and on requested names, and returns `requested_count`.
- `SecretService.on_all_set: Callable[[str], None]` defaults to a no-op. `put()` calls it when the project's row has `start_when_ready` and `requested_count` has just reached 0.
- `SecretService.list()` entries in `secrets` gain `"unused": bool`.
- `LifecycleService.start_work(project_id, *, stop_first, release: bool = True)`. With `release=False` the work does not release the lock. Every start clears `start_when_ready` before compose runs, and `stop()` clears it too.
- `LifecycleService.down_now(project_id) -> None` runs compose down and `set_status("stopped")`, raising `JobFailed`; no lock handling.
- `LifecycleService.start_when_ready(project_id) -> dict`. If nothing is requested, it starts the project and returns `{"job_id"}`. Otherwise it sets the flag and returns `{"waiting": n}`.
- Route `POST /projects/{project_id}/start-when-ready` in `rest/routers/lifecycle.py`, mounted at both prefixes like the other lifecycle routes.
- The project payload gains `"start_when_ready": bool(row["start_when_ready"])`.
- `wiring`: `secrets.on_all_set = lambda pid: _start_quietly(lifecycle, pid)`. A `project_busy` Conflict is logged, not raised.

- [ ] **Step 1: Write the failing tests**

```python
def test_start_when_ready_waits_then_starts_on_the_last_secret(env):
    _write_compose(env, "blog"); _create(env, "blog")
    env.client.post("/projects/blog/secrets/requests", json={"name": "A", "hint": "h"})  # match the existing request route
    env.client.post("/projects/blog/secrets/requests", json={"name": "B", "hint": "h"})
    assert env.client.post("/projects/blog/start-when-ready").json() == {"waiting": 2}
    env.client.put("/projects/blog/secrets/A", json={"value": "1"})
    assert not any(a[-2:] == ["up", "-d"] for a in env.runner.calls)
    env.client.put("/projects/blog/secrets/B", json={"value": "2"})
    # wait for the job: env.jobs.active_for("blog") then env.jobs.wait(...)
    assert any(a[-2:] == ["up", "-d"] for a in env.runner.calls)
    assert env.client.get("/projects/blog").json()["start_when_ready"] is False

def test_start_when_ready_with_nothing_requested_starts_now(env): ...  # returns {"job_id": ...}
def test_saving_the_last_secret_without_the_flag_does_not_start(env): ...
def test_apply_pushed_marks_unused_and_requests_missing(env): ...
    # stored OLD + A; apply_pushed({"A": "", "NEW": "hint"}) -> requested [NEW], secrets A unused False, OLD unused True; returns 1
def test_stop_clears_start_when_ready(env): ...
```

Check the exact secrets request and put routes in `rest/routers/secrets.py` before writing them.

- [ ] **Step 2: Run them and confirm they fail.** Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_api_secrets.py tests/runtime/api/test_lifecycle.py -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and confirm they pass,** along with the whole `tests/runtime/api -q`.
- [ ] **Step 5: Commit** (`Secrets: pushed names, unused marks, start once secrets are set`)

---

### Task 7: Kitchen HTTP client

**Files:**
- Create: `runtime/eggie_api/infra/kitchen.py`
- Modify: `runtime/eggie_api/config.py`: `kitchen_ca_file: Path | None = None` (env `EGGIE_KITCHEN_CA_FILE`, empty means None) and `transfers_root: Path = Path(f"{GUEST_ROOT}/transfers")` (env `EGGIE_TRANSFERS_ROOT`).
- Test: `tests/runtime/api/test_kitchen_client.py`, `tests/runtime/api/test_config.py`

**Interfaces:**
- `class KitchenError(Exception)`: `code`, `message`, `status`, `body: dict` (the envelope's `error`).
- `class KitchenUnreachable(Exception)`.
- `KitchenClient(*, opener=None, ca_file: Path | None = None, timeout: float = 30.0)`. The default opener is a `urllib.request.build_opener(HTTPSHandler(context=ssl.create_default_context(cafile=None)))`; when `ca_file` is given, the context also calls `load_verify_locations(ca_file)`. There is never a setting that turns verification off.
- `open(host, *, ticket: str, size: int, data: bool, replace: str) -> dict` sends `POST https://{host}/api/transfers` with the headers `Eggie-Transfer-Ticket`, `Eggie-Transfer-Size`, `Eggie-Transfer-Data: yes|no` and `Eggie-Transfer-Replace`, and returns `{push_id, token}`.
- `upload(host, push_id, token, path: Path, on_progress: Callable[[int], None]) -> None` sends `PUT …/api/transfers/{id}/bundle` with `Content-Length`. The body is a file wrapper whose `read()` reports the bytes sent so far.
- `status(host, push_id, token) -> dict`.
- Error mapping:
  - a JSON envelope raises `KitchenError`;
  - `OSError`, `ssl.SSLError` and `http.client.HTTPException` raise `KitchenUnreachable`;
  - on `open()`, a 404 or a 401 `not_signed_in` (an older kitchen, whose middleware answers first) raises `KitchenUnreachable("older")`.

- [ ] **Step 1: Write the failing tests** with a fake opener that records each `Request`:
  - `test_open_sends_ticket_only_in_a_header` asserts the ticket is in `headers`, not in `full_url`, and that the four headers are present;
  - `test_upload_reports_progress_and_sends_content_length` uses a 3-chunk file and asserts the `on_progress` values rise to the size;
  - `test_ticket_refusal_is_a_kitchen_error_with_the_service_code` asserts that a 403 envelope gives `KitchenError` with `.code == "transfer_ticket_invalid"`;
  - `test_old_kitchen_and_connection_errors_are_unreachable` covers a 401 `not_signed_in` on open and a `URLError`.
- [ ] **Step 2: Run them and confirm they fail.** Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_kitchen_client.py tests/runtime/api/test_config.py -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Commit** (`Kitchen client: open, upload, status over verified TLS`)

---

### Task 8: Local push service, job and routes

**Files:**
- Create: `runtime/eggie_api/services/push.py`, `runtime/eggie_api/rest/routers/push.py`
- Modify: `services/jobs.py` (progress), `rest/schemas.py` (`PushRequest`), `rest/app.py` (mount `push.build_console` at `/api` only), `services/projects.py` (payload `push`), `wiring.py` (`kitchen=` fake kwarg, `PushService`, `Services.push`), `tests/runtime/api/conftest.py` (`_make_env(..., cloud=None, kitchen=None)` passed to `create_app`)
- Create: `tests/runtime/api/fake_kitchen.py`
- Test: `tests/runtime/api/test_push.py`

**Interfaces:**
- `Job.progress: dict | None` is included in `as_dict()`. `_Output.progress(done: int, total: int)`.
- `PushService(config, runner, loader, projects: ProjectRepo, secrets: SecretService, lifecycle: LifecycleService, jobs, locks, account, cloud, kitchen: KitchenClient, cloud_projects, kitchen_pushes, wake, *, clock=time.time, sleep=time.sleep)`.
- `PushService.targets(project_id) -> dict`, the shape in spec §4.1. It maps `NotSignedIn` to `Conflict("not_signed_in")`, `CloudUnavailable` to `Unavailable("cloud_unavailable")`, and other `CloudError`s to `Upstream("cloud_error")`. `databases` comes from `domain.push.databases(loader.compose(id))`. `running` means status is `started_ok`.
- `PushService.start(project_id, instance_id, *, data: bool, replace_data: bool) -> str` (job id, kind `"push"`). Before the job it requires the project and a compose file, and raises `Conflict("confirm_replace_data")` when `data` is set, a record exists for that instance and `replace_data` isn't set. It holds the lock through `lifecycle.submit_locked`.
- `PushService.summary(project_id) -> dict` returns `{"blocked": "site" | "signed_out" | None, "last": {instance_id, console_hostname, pushed_at} | None}` (`last` is the latest record). `ProjectService` takes a `push_summary: Callable[[str], dict]` and puts it in the payload as `"push"`.
- `FakeKitchen` scripts `open`, `upload` and `status` replies like `FakeCloud`, and records the calls.
- Job phases, in order: `preparing`, `stopping`, `packing`, `starting`, `sending`, then the kitchen's own phases, then the result.
- Job result: `{instance_id, kitchen, kitchen_project, urls, secrets_missing, open_secrets_url, local_restart}`. Here `kitchen` is the display name, `local_restart` is `"ok" | "failed" | None`, and `open_secrets_url` is `open_console_url + "&return_path=" + quote("/p/<kitchen_project>/secrets", safe="")` when `secrets_missing > 0`, else `None`.
- Failures are `JobFailed(message, result={"code": <code>, "retry_after": <s|None>})`, using the codes and copy of spec §6.

- [ ] **Step 1: Write the failing tests.** Use `env` with `FakeCloud` and `FakeKitchen`. Add `FakeRunner.on_docker_run: Callable[[list[str]], Completed] | None` to `conftest.py`. The push tests set it to a hook that, for a `pack` argv, calls `write_bundle` on the real temp project to the argv's `--out`, mapped from `/out` to `config.transfers_root/"out"`, and answers `Completed(0, "", "")`.

```python
def test_targets_merge_service_kitchens_with_local_records(env): ...
    # kitchens unchanged, pushed has the k1 record, databases lists db floating
def test_push_happy_path_mints_opens_uploads_and_records(env): ...
    # cloud.calls: transfer_targets, mint(…, "k1", "c-shop"); kitchen calls: open(replace="none", data=True), upload, status x2
    # repos.kitchen_pushes.for_project("shop")["k1"]["kitchen_project"] == "shop"
    # job result open_secrets_url ends with "&return_path=%2Fp%2Fshop%2Fsecrets" when secrets_missing=2
def test_record_is_written_when_the_kitchen_accepts_the_upload(env): ...
    # status replies raise KitchenUnreachable -> job failed, but kitchen_pushes has k1
def test_repeat_push_with_data_needs_confirmation(env): ...  # 409 confirm_replace_data
def test_ticket_refused_is_retried_exactly_once(env): ...  # two mints, second refusal fails the job with transfer_ticket_invalid
def test_already_on_kitchen_is_recorded_and_not_retried(env): ...  # one mint; record exists after; code already_on_kitchen
def test_unreachable_kitchen_gets_three_attempts_with_fresh_tickets(env): ...
def test_rate_limit_reports_retry_after(env): ...  # mint raises CloudError(..., 429, retry_after=120) -> result retry_after 120
def test_data_push_stops_and_restarts_a_running_project(env): ...  # down before pack argv, up after
def test_unregistered_project_fails_with_not_registered(env): ...  # no cloud mapping, sleep stubbed
def test_sign_out_mid_push_fails_with_not_signed_in(env): ...
def test_project_lock_is_held_for_the_whole_push(env): ...  # POST /api/.../up during push -> 409 project_busy
def test_push_routes_are_console_only(env): ...  # GET /projects/shop/push with the bearer token -> 404
def test_staging_file_is_removed_after_the_push(env): ...
```

Fill in each `...` with the assertions in its comment. Browser calls use the existing `BROWSER` host plus a session, as in `test_api_public.py`.

- [ ] **Step 2: Run them and confirm they fail.** Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_push.py -q`.
- [ ] **Step 3: Implement.**
  - Registration wait: call `wake()`, then poll `cloud_projects.mapping()` every 1 s for 15 s through the injected `sleep`.
  - Free disk: refuse with `DiskFull("disk_full")` when the free bytes are below the project's `tree_stats` bytes plus the volume sizes (from `docker system df -v --format json`) plus 512 MiB.
  - Sweep `config.transfers_root/"out"` in `wiring.build`.
- [ ] **Step 4: Run the tests and confirm they pass,** along with the whole `tests/runtime/api -q` and `tests/runtime/api/route_sweep.py`'s test, if it enumerates routes.
- [ ] **Step 5: Commit** (`Push: local job, targets and console routes`)

---

### Task 9: Kitchen transfer entry point, import job and readiness

**Files:**
- Create: `runtime/eggie_api/services/transfers.py`, `runtime/eggie_api/rest/routers/transfers.py`, `runtime/eggie_api/services/ratelimit.py`
- Modify:
  - `rest/auth.py`: a branch for `/api/transfers*` (`Host` must be `site_host`, else 403 `forbidden_host`; then `call_next` with no cookie or Origin check), and `("GET", "/api/ready")` added to `open_browser_routes`;
  - `rest/app.py`: `if config.site_host: app.include_router(transfers.build(services))`;
  - `services/site.py`: use `ratelimit.Window`;
  - `services/projects.py`: `delete()` also calls `pushed_projects.remove`;
  - `wiring.py`: `Transfers` and `Services.transfers`; sweep `transfers_root/"in"`.
- Test: `tests/runtime/api/test_transfers.py`

**Interfaces:**
- `ratelimit.Window(limit: int, seconds: float, clock=time.time)` with `take() -> bool`. It warns once per run of refusals, as `SiteSignIn._take_remote_slot` does.
- `Transfers(config, runner, loader, projects, pushed_projects, secrets, lifecycle, jobs, locks, account, cloud, *, free_bytes: Callable[[], int], clock=time.time)`.
- `Transfers.open(*, ticket: str, size: int, data: bool, replace: str) -> dict` returns `{push_id, token}` (201). It runs the steps of spec §5.1. Errors:
  - `Forbidden("transfer_ticket_invalid", <the service's message>)`;
  - `Unavailable("cloud_unavailable")`;
  - `Conflict("kitchen_busy")` when the project lock is held, and `TooMany("kitchen_busy")` when the rate limit trips (a new `TooMany(EggieError)` class, mapped to 429 in `rest/errors.py`);
  - `Conflict("already_on_kitchen" | "confirm_replace_data")`;
  - `DiskFull("kitchen_disk_full")`;
  - `Invalid("bad_transfer")` for a bad header value.
  - It claims `pushed_projects` for a resolved id before answering, and creates the project row (`projects.add`) for a new id.
- `Transfers.receive(push_id, token, save: Callable[[Path], Awaitable[Path]], content_length: int) -> dict` (async) returns `{"push_id"}` (202). It checks the token and that `content_length` equals the declared size, saves to `transfers_root/"in"/f"{push_id}.tar"`, and submits the import job (kind `"import"`) with the lock already held. Any failure deletes the file, releases the lock, drops the push and re-raises.
- `Transfers.status(push_id, token) -> dict` returns `{state, phase, detail, result}`. An unknown id or a bad token raises `NotFound("transfer_not_found")`.
- `Transfers.ready() -> bool` is `account.signed_in and docker version ok`.
- Router (`build(s)`):
  - `POST /api/transfers` reads the four headers. A missing `Eggie-Transfer-Size` raises a new `LengthRequired(EggieError)`, mapped to 411; any other missing or bad header raises `Invalid("bad_transfer")`;
  - `PUT /api/transfers/{push_id}/bundle`;
  - `GET /api/transfers/{push_id}`;
  - `GET /api/ready` answers 200 `{"ready": true}` or `Unavailable("not_ready")`.
- An opened push expires after 600 s with no upload. `open()` checks for expired pushes and frees their lock first.
- Import job: spec §5.3 steps 1–7.
  - Unpack with `files.extract_archive`; volumes go through `bundle.restore_argv` and the runner; the old folder is removed with `docker.remove_tree_as_root` as a fallback.
  - `complete` is retried 3 times on `CloudUnavailable` with sleeps of 1, 3 and 9 s (injected sleep).
  - Result: `{kitchen_project, urls, secrets_missing}`.
  - With no secrets missing, `lifecycle.start(id)` runs after the lock is released. Otherwise `projects.set_start_when_ready(id, True)`.

- [ ] **Step 1: Write the failing tests** (`site_env` with a `FakeCloud`; a bundle built with `write_bundle` from Task 5)

```python
H = {"Host": "foo.eggie.io"}
def test_transfer_routes_exist_only_on_a_site(env): ...            # POST /api/transfers locally -> 401 not_signed_in or 404
def test_wrong_host_is_refused_before_anything(site_env): ...      # Host other -> 403 forbidden_host, cloud.calls == []
def test_api_token_and_session_are_not_credentials(site_env): ...  # Authorization: Bearer <api token>, no ticket -> 400/422 bad_transfer, no redeem
def test_refused_ticket_relays_the_service_envelope(site_env): ... # redeem raises CloudError("transfer_ticket_invalid", "m", 403) -> 403 same code
def test_existing_project_needs_replace(site_env): ...             # claim c-1 first; replace=none -> 409 already_on_kitchen; lock free afterwards
def test_data_into_existing_needs_files_and_data(site_env): ...
def test_disk_check_uses_twice_the_size_plus_a_gib(site_env): ...  # free_bytes fake
def test_name_taken_by_a_kitchen_project_gets_a_suffix(site_env): ...
def test_full_import_replaces_files_keeps_data_paths_and_completes(site_env): ...
    # existing project with pgdata/old and src/old.py; push without data -> src/old.py gone, pgdata/old kept,
    # cloud.calls ends with complete_transfer(token, "t-1"); pushed_projects.last_pushed_at set
def test_import_with_missing_secrets_waits_instead_of_starting(site_env): ...
def test_complete_project_not_found_fails_and_keeps_files(site_env): ...
def test_failed_upload_releases_the_lock(site_env): ...            # content-length mismatch -> error; next open not kitchen_busy
def test_status_needs_the_token_and_hides_unknown_ids(site_env): ...
def test_rate_limit_answers_429(site_env): ...
def test_ready_reflects_sign_in_and_docker(site_env): ...
def test_ticket_never_reaches_a_log_line(site_env, caplog): ...
```

Fill in each `...` with the assertions in its comment. Use `raw_client` where a non-2xx is expected.

- [ ] **Step 2: Run them and confirm they fail.** Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_transfers.py tests/runtime/api/test_site.py tests/runtime/api/test_api_browser_auth.py -q`.
- [ ] **Step 3: Implement.** Keep the replace order: move the old folder aside, move the new tree in, move `data_paths` back, then remove the old folder. On an exception, rename the old folder back before re-raising.
- [ ] **Step 4: Run the tests and confirm they pass,** along with the whole `tests/runtime/api -q` and `tests/test_no_platform_leak.py`.
- [ ] **Step 5: Commit** (`Kitchen: transfer entry point, import job, /api/ready`)

---

### Task 10: Sign-in lands on a page (`return_path`)

**Files:**
- Modify: `runtime/eggie_api/services/site.py`, `rest/routers/sessions.py`, `runtime/web/apps/console/src/boot/boot.ts`, `runtime/web/apps/console/src/main.tsx` (if the path is applied there)
- Test: `tests/runtime/api/test_site.py`, `runtime/web/apps/console/src/boot/boot.test.ts`

**Interfaces:**
- `SiteSignIn.redeem(code) -> tuple[str, str | None] | None` returns `(session_id, return_path)`. A local code gives `None` as the path. The service's `return_path` passes through `domain.push.safe_return_path`.
- `POST /api/session` answers `{"signed_in": true, "return_path": <str|null>}`.
- Console: `landingPath(value: unknown): string | null` in `boot/boot.ts`. It returns the value only if it is a string that starts with `/` and whose second character is neither `/` nor `\`. The boot result for `signedIn` gains `landing: string | null`, and `App` calls `window.history.replaceState(null, "", landing)` before mounting `BrowserRouter`.

- [ ] **Step 1: Write the failing tests:**
  - `test_remote_code_returns_the_services_return_path` (the fake redeem answers `{"ok": true, "return_path": "/p/shop/secrets"}`);
  - `test_unsafe_return_path_from_the_service_is_dropped` (`"//evil.com"` gives `None`);
  - Vitest `landingPath` cases (`"/p/a/secrets"` passes; `"//x"`, `"/\\x"`, `"https://x"` and `42` give `null`), and the boot result carries `landing` from the `/api/session` answer.
- [ ] **Step 2: Run them and confirm they fail.** Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_site.py -q` and `cd runtime/web && npx vitest run apps/console/src/boot/boot.test.ts`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Commit** (`Kitchen sign-in returns to the page it was opened for`)

---

### Task 11: Console push logic (pure)

**Files:**
- Create: `runtime/web/apps/console/src/push/push.ts`, `push/push.test.ts`
- Modify: `projects/types.ts`: `JobKind` gains `"push" | "import"`; `Job.progress?: {done: number; total: number} | null`; `Project.push: PushSummary`; `Project.start_when_ready: boolean`; the `SecretsView.secrets[]` entries gain `unused: boolean`.

**Interfaces:**
- Types:
  - `TargetKitchen` = `{id, display_name, console_hostname, site_hostname, readiness, is_owner, open_console_url, manage_url}`;
  - `PushTargets` = `{kitchens: TargetKitchen[], create_kitchen_url, pushed: Record<string, PushRecord>, databases: {service, image, floating}[], running: boolean}`;
  - `PushRecord` = `{pushed_at, console_hostname, kitchen_project: string | null, urls: string[], secrets_missing: number}`;
  - `PushSummary` = `{blocked: "site" | "signed_out" | null, last: {instance_id, console_hostname, pushed_at} | null}`.
- `kitchenRows(t: PushTargets): KitchenRow[]` returns `{id, label, host, note: string | null, pickable: boolean, pushedAt: number | null, manageUrl: string | null}`. Notes, in this order: `"Owned by a teammate"`; `starting` gives `"Starting…"`; `failed` gives `"Failed"` with `manageUrl`; `signed_out` gives `"Signed out"` with `manageUrl`; any other value that isn't `ready` gives `"Not available"`.
- `pushChoice(row: KitchenRow, data: boolean, confirmed: boolean): {repeat: boolean; needsConfirm: boolean; canPush: boolean}`.
- `defaultData(row: KitchenRow): boolean` returns `row.pushedAt === null`.
- `secretsUrl(openConsoleUrl: string, kitchenProject: string): string`.
- `floatingWarnings(t: PushTargets): string[]` uses the spec's copy.
- `menuPush(project: Project, online: boolean, pushingPercent: number | null): {note: string | null; disabled: boolean} | null` returns `null` on a site.
- `pushErrorMessage(code: string, ctx: {kitchen: string; project: string; retryAfter?: number | null}): string` uses the copy of spec §6.

- [ ] **Step 1: Write the failing tests**

```ts
it("only ready kitchens the user owns can be picked", ...)        // rows for ready+owner, ready+teammate, starting, failed, "weird"
it("a pushed kitchen defaults data off and marks the row", ...)
it("data on a repeat push needs the confirmation", () => {
  expect(pushChoice(pushedRow, true, false)).toEqual({ repeat: true, needsConfirm: true, canPush: false });
  expect(pushChoice(pushedRow, true, true).canPush).toBe(true);
  expect(pushChoice(freshRow, true, false)).toEqual({ repeat: false, needsConfirm: false, canPush: true });
});
it("secretsUrl appends an encoded return path", () =>
  expect(secretsUrl("https://app.eggie.io/kitchens/open?host=k.eggie.io", "my-shop"))
    .toBe("https://app.eggie.io/kitchens/open?host=k.eggie.io&return_path=%2Fp%2Fmy-shop%2Fsecrets"));
it("menu item: account required, offline, empty, pushing", ...)
it("rate limit message names the wait in minutes", ...)          // retryAfter 120 -> "Try again in 2 minutes."
```

- [ ] **Step 2: Run them and confirm they fail.** Run: `cd runtime/web && npx vitest run apps/console/src/push/push.test.ts`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and confirm they pass,** along with `npm run typecheck`. Fix the mocks and fixtures that build `Project` (add `push` and `start_when_ready`).
- [ ] **Step 5: Commit** (`Console: push picker rules and copy`)

---

### Task 12: Console push dialog, menu item and mocks

**Files:**
- Create: `runtime/web/apps/console/src/screens/project/PushModal.tsx`
- Modify:
  - `screens/project/ProjectMenu.tsx`: the item after Public address; `ProjectDialog` gains `"push"`;
  - `screens/project/ProjectLayout.tsx`: renders `PushModal`;
  - `projects/queries.ts`:
    - `usePushTargets(id, open: boolean)` refetches on every open, `refetchInterval` 5000 while any kitchen is `starting`;
    - `useStartPush(id)` posts `{instance_id, data, replace_data}`;
    - `usePushJob(project)` follows `project.job` when its kind is `push`;
  - `mocks/handlers.ts`: the scenarios `push-no-account`, `push-offline`, `push-no-kitchens`, `push-kitchens`, `push-running`, `push-done-secrets` and `push-rate-limited`, with handlers for `GET/POST /api/projects/:id/push` and the push job;
  - `projects/view.ts`: a `push` job does not change the screen state.
- Test: none new beyond Task 11. The UI is checked by hand with the scenarios (Step 3).

**Interfaces:**
- Consumes Task 11's functions; `openExternal` from `desktop/desktop.ts`; `StartingBody`'s pieces (`ProgressBar`, `Elapsed`, `phaseCaption` or a push variant in `projects/copy.ts`).
- Dialog states: loading; error (`pushErrorMessage`); no kitchens ("Create a kitchen"); picker (with a "Create a new kitchen" link under the list, also through `openExternal`); running (phase caption + elapsed + bytes bar from `job.progress`); done (URLs with Open buttons, plus "Enter N secrets on the kitchen" from `result.open_secrets_url`, or else from `PushRecord`); failed.

- [ ] **Step 1: Build the dialog and the item,** following `PublicModal.tsx`'s structure and the spec §3 copy.
- [ ] **Step 2: Run `npm run typecheck` and `npm test`.** Expected: PASS.
- [ ] **Step 3: Check every scenario in a browser.** Run `npm run dev`, then open `/p/<id>?scenario=<each push-* scenario>`, using the headless Chrome + Playwright setup already used for this console. Each state must match the spec §3 table, in light and dark.
- [ ] **Step 4: Commit** (`Console: Push to Kitchen dialog`)

---

### Task 13: Console waiting-for-secrets and unused names

**Files:**
- Modify:
  - `screens/list/ProjectRow.tsx`: a "Needs N secrets" `Badge` while `secrets_requested > 0`;
  - `screens/project/ProjectPage.tsx`: Start, when `secrets_requested > 0`, calls `useStartWhenReady` and navigates to `secrets`;
  - `screens/secrets/SecretsPage.tsx`: a "Starts once these are set" notice plus a "Start without them" button (the ordinary `up`) when `start_when_ready`; a "Not used by the latest push" section for `unused` names, with Delete;
  - `projects/queries.ts`: `useStartWhenReady(id)`;
  - `mocks/handlers.ts`: the scenario `kitchen-waiting-secrets`.
- Test: `projects/secrets.test.ts`: a pure helper `splitSecrets(view): {used, unused}`.

- [ ] **Step 1: Write the failing test** for `splitSecrets` (`unused` entries are separated and order is kept).
- [ ] **Step 2: Run it and confirm it fails.** Run: `cd runtime/web && npx vitest run apps/console/src/projects/secrets.test.ts`.
- [ ] **Step 3: Implement the helper and the UI changes.**
- [ ] **Step 4: Run `npm test` and `npm run typecheck`, and check `?scenario=kitchen-waiting-secrets` by hand.** Start must land on the form, "Start without them" must start the project, and the badge must disappear once the requests are filled.
- [ ] **Step 5: Commit** (`Console: wait for secrets before starting, unused names`)

---

### Task 14: Docs

**Files:**
- Modify:
  - `docs/security.md`: "Site mode" gains the transfer entry point; a new short section, "Pushing to a kitchen", covers what leaves the machine;
  - `docs/site-mode.md`: the transfers endpoints the server calls, `/api/transfers`, `/api/ready`, `return_path`, pushed projects kept out of sync;
  - `docs/release-testing.md`: append the next free S-series IDs for the six cases in spec §8;
  - `runtime/eggie_api/CLAUDE.md`: a "Push / Transfers" feature-area entry;
  - `runtime/web/CLAUDE.md`: a line on `push/` and the mock scenarios.
- [ ] **Step 1: Write the doc changes,** a few bullets each, naming the routes, headers, codes and files exactly as built.
- [ ] **Step 2: Verify:**
  - `TMPDIR=/home/ihor/tmp python3 -m pytest -q` (the full suite passes);
  - `cd runtime/web && npm test && npm run typecheck`;
  - `grep -n "S[0-9]" docs/release-testing.md`, which must show that no existing ID changed.
- [ ] **Step 3: Commit** (`Docs: push to kitchen`)
