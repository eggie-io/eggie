# Runtime Update Notice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While the VM is up, it checks for a newer runtime release, and the console header offers "Update available". When the user clicks Update now, the runtime updates in place. This matters most on cloud servers, which rarely reboot. Local VMs work the same way.

**Architecture:** A root systemd timer runs `update-check.sh`, which uses the installed `get.sh`'s `resolve_ref` and the boot updater's `accepted_api` to write `/opt/eggie/update/available.json`. The API reads that file, the live `runtime.version` and the updater's `status.json`. On a click, the API bumps a `request` counter, and a root path unit runs `update-now.sh`, which wraps the existing `boot-update.sh` and records the outcome. The console polls `GET /api/runtime/update`, shows a header pill, and while an update runs it shows an overlay until the installed version changes, then reloads.

**Tech Stack:** bash + systemd units (runtime/install), FastAPI (runtime/eggie_api), React + TanStack Query + Vitest (runtime/web).

**Spec:** Issue eggie-io/eggie-resources#148. The design was approved in conversation and is restated below.

## Design (approved)

- A pill in the console header, next to the account menu, shown only when a newer runtime exists. The user can ignore it, and the boot updater still runs at every boot as today.
- The pill opens a panel: "Eggie X.Y.Z is available", the installed version, and **Update now**. After a failed attempt, the panel shows the reason and the button reads **Try again**. The old release stays running.
- During an update, an overlay shows "Updating Eggie…". It explains that Eggie restarts for about a minute and that projects keep running. When the installed version changes, the page reloads.
- The check runs about 10 min after boot, then every 6 h (with a random delay). It offers only releases this machine accepts (the boot updater's rule), and never offers one to a branch-pinned install.
- Anyone signed in to the console may click Update. The routes are console-only (`/api`), like Public URL.
- Out of scope: automatic updates while the VM runs, "skip this version", release notes.

## Global Constraints

- The file contract lives under `/opt/eggie/update/`, created `2770 root:docker`:
  - `available.json` (root writes) = `{"available": "runtime-vX.Y.Z" | null, "checked_at": <epoch int>}`
  - `request` (API writes) = decimal counter, mode `0660`
  - `status.json` (root writes) = `{"request": <int>, "state": "running" | "done" | "failed", "error": <string|null>}`
- Root writes every JSON file atomically (temp file in the same dir, then `mv`) and makes it group-readable (`0640`; the setgid dir makes the group `docker`).
- The API's wire shape is `{"installed": "X.Y.Z"|null, "available": "X.Y.Z"|null, "state": "idle"|"running"|"failed", "error": string|null}`. Versions drop the `runtime-v` prefix.
- User-facing text says "Eggie", never "runtime".
- Python tests: `TMPDIR=/home/ihor/tmp python3 -m pytest …` from the repo root. Console: `cd runtime/web && npx vitest run <file>` and `npm run typecheck`.
- Tests reach repo files via `__file__`, never cwd-relative paths. Follow `/home/ihor/.claude/CLAUDE.md` testing rules: no framework or trivial-glue tests.
- Comments only for edge cases and workarounds, two lines at most, no ticket references.

## Review Focus

1. **The API container is recreated during the update.** All state must live in files, not in the API's memory. The console must survive failed fetches while the API is down (keep the last data and keep polling). Covered in Task 2 (state from files only) and Task 3 (`updateView` with `startedAt`).
2. **A request the root unit never picks up, or an updater killed mid-run** (reboot, OOM), would otherwise show "Updating…" forever. Pending for more than 2 min becomes `failed` ("The updater inside Eggie did not start."), and `running` for more than 30 min becomes `failed` ("The update did not finish."). Task 2 tests both.
3. **The boot updater got there first**, so `available.json` is stale and names a release ≤ the installed one. The API must hide it (Task 2), and the console must not offer it.
4. **The network is down during a check.** The check must exit non-zero and leave the previous `available.json` untouched, never write `null` over a real offer (Task 1).
5. **A double click, or a second tab, while an update runs.** POST refuses with `409 update_running`, and the counter is not bumped twice (Task 2).

---

### Task 1: In-VM check and update runner

**Files:**
- Create: `runtime/install/lib/update-check.sh`, `runtime/install/lib/update-now.sh`
- Create: `runtime/install/systemd/eggie-update-check.service`, `eggie-update-check.timer`, `eggie-update-now.path`, `eggie-update-now.service`
- Modify: `runtime/install/install.sh` (new step after 11b), `runtime/install/snapshot-prep.sh` (strip `$ROOT/update`)
- Modify: `runtime/install/CLAUDE.md` (section for the two scripts and units, test list)
- Test: `tests/runtime/test_update_check.py`, `tests/runtime/test_update_now.py`, additions to `tests/runtime/test_install_shell.py` and `tests/runtime/test_snapshot_prep.py`

**Interfaces:**
- Produces: the files in Global Constraints, at `/opt/eggie/update/`.
- `update-check.sh [update-dir]` (default `/opt/eggie/update`):
  - Reads `/opt/eggie/runtime.version`. If that is not `^runtime-v[0-9]+\.[0-9]+\.[0-9]+$` (missing, or a branch), write `available: null` and exit 0 without calling git.
  - Otherwise source `$LIB/boot-update.sh` with `EGGIE_BOOT_UPDATE_SOURCED=1` for `accepted_api`, source `/opt/eggie/runtime.env`, and source `$LIB/../get.sh` (its trailing `(return 0) || main` keeps main from running).
  - Run `resolve_ref "$EGGIE_RUNTIME_REPO" /dev/null` with `EGGIE_RUNTIME_API=<accepted>` and `EGGIE_RUNTIME_REF`/`EGGIE_RUNTIME_REPAIR` unset.
  - Any failure → exit non-zero and leave the file alone.
  - Write `available = ref` only when `sort -V` puts it strictly above the installed ref; otherwise write `null`.
  - `LIB` is the script's own dir, as in `agents-run.sh`.
- `update-now.sh [update-dir]`:
  - `N` = int in `request` (0 if missing or garbage). `H` = `status.json`'s `request` (0 if missing). If `N <= H`, exit 0 and do nothing.
  - Otherwise write `{"request":N,"state":"running","error":null}` and run `bash "$LIB/boot-update.sh"`. Stderr goes to the journal and is kept in a temp file.
  - Exit 0 → `state: "done"`. Non-zero → `state: "failed"`, with `error` = the last non-empty stderr line, trimmed, at most 300 chars (`"no reason given"` when empty).
  - Then run `bash "$LIB/update-check.sh" "$DIR"` and ignore its result. After a swap, `$LIB` resolves to the new release's copy.
  - Build the JSON with `python3 -c 'import json,sys;…'` (python3 is on every image) so the error string is escaped correctly.
- Units:
  - `eggie-update-check.timer`: `OnBootSec=10min`, `OnUnitActiveSec=6h`, `RandomizedDelaySec=30min`.
  - `eggie-update-check.service`: `Type=oneshot`, `ExecStart=/bin/bash /opt/eggie/runtime/install/lib/update-check.sh`.
  - `eggie-update-now.path`: `PathChanged=/opt/eggie/update/request`.
  - `eggie-update-now.service`: `Type=oneshot`, `StartLimitIntervalSec=0`, `ExecStart=/bin/bash /opt/eggie/runtime/install/lib/update-now.sh`.
  - The path and timer units are `WantedBy=multi-user.target` / `timers.target`.
- `install.sh` new step **11b-2**, after 11b and before 11c:
  - `install -d -m 2770 -o root -g docker /opt/eggie/update` + `chmod 2770 /opt/eggie/update`
  - install the 4 units, `systemctl daemon-reload`, `systemctl enable --now eggie-update-now.path`, `systemctl enable --now eggie-update-check.timer`
  - never `start eggie-update-now.service` (that would run an update inside this install)

Tests copy `runtime/install/get.sh` and `lib/` into a tmp tree with `/opt/eggie` rewritten to a tmp root, as `test_boot_update.py` does, with fake `git`/`curl` on `PATH` (see `test_get_sh.py` for the resolve_ref fakes). For `update-now.sh`, put fake `boot-update.sh`/`update-check.sh` siblings in the tmp `lib/`.

- [ ] **Step 1: Write failing tests**
  - `test_update_check.py`:
    - `test_a_newer_accepted_release_is_offered`: installed `runtime-v0.1.0`; tags `0.1.0`, `0.2.0`, both `api 1`; `host.json` `[1]` → `available == "runtime-v0.2.0"` and `checked_at` is an int.
    - `test_the_installed_release_is_not_offered`: only tag `0.1.0` → `available is None`.
    - `test_a_branch_pin_is_never_offered_and_git_is_not_asked`: installed `dev` → `None`, and the fake git log is empty.
    - `test_an_unreachable_repo_keeps_the_last_answer`: pre-existing `available.json` naming `runtime-v0.2.0`, failing fake git → non-zero exit, file byte-identical.
  - `test_update_now.py`:
    - `test_a_new_request_runs_the_updater_and_records_done`: `request` = `3`, no status → fake boot-update ran once, status `{"request":3,"state":"done","error":null}`, fake update-check ran.
    - `test_a_failed_update_records_the_last_error_line`: fake boot-update prints `x` then `could not download the images for Eggie runtime runtime-v0.2.0; staying on runtime-v0.1.0` to stderr and exits 1 → `state == "failed"`, `error` equals the last line.
    - `test_an_already_handled_request_does_nothing`: `request` = `3`, status request 3 → fake boot-update not run, status unchanged.
  - `test_install_shell.py`: `install -d -m 2770 -o root -g docker /opt/eggie/update`, `enable --now eggie-update-now.path` and `enable --now eggie-update-check.timer` appear before the marker write, and `start eggie-update-now` does not appear. Both scripts pass `bash -n`.
  - `test_snapshot_prep.py`: the `update` dir is removed (follow the existing assertion for `agent-status`).
- [ ] **Step 2: Run them and confirm they fail.** `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_update_check.py tests/runtime/test_update_now.py tests/runtime/test_install_shell.py tests/runtime/test_snapshot_prep.py -q` → the new tests FAIL.
- [ ] **Step 3: Implement the scripts, units, install step, snapshot-prep line, and the CLAUDE.md section.**
- [ ] **Step 4: Run the same command, then the full suite** `TMPDIR=/home/ihor/tmp python3 -m pytest -q` → all PASS.
- [ ] **Step 5: Commit** `feat(runtime): check for newer runtime releases and run an update on request`

### Task 2: API — `GET/POST /api/runtime/update`

**Files:**
- Create: `runtime/eggie_api/services/runtime_update.py`, `runtime/eggie_api/rest/routers/runtime_update.py`
- Modify: `runtime/eggie_api/constants.py` (`UPDATE_DIR = f"{GUEST_ROOT}/update"`, `RUNTIME_MARKER = f"{GUEST_ROOT}/runtime.version"` unless one already exists), `config.py` (`update_dir: Path`, `runtime_marker: Path`, from env `EGGIE_UPDATE_DIR`/`EGGIE_RUNTIME_MARKER` like the neighbours), `wiring.py` (`Services.runtime_update`), `rest/app.py` (mount `runtime_update.build_console(services)` at `/api` only, next to `instances`)
- Modify: `runtime/eggie_api/CLAUDE.md` (a **Runtime update** feature-area bullet)
- Test: `tests/runtime/api/test_runtime_update.py`

**Interfaces:**
- Consumes: Task 1's files.
- Produces: `class RuntimeUpdate(update_dir: Path, marker: Path, *, clock: Callable[[], float] = time.time)` with `status() -> dict` and `request() -> dict`. Both return the wire shape from Global Constraints. Routes: `GET /api/runtime/update` → `status()`; `POST /api/runtime/update` → `request()`.
- `status()` rules, in order:
  - `installed` comes from the marker (only `runtime-vX.Y.Z` parses; otherwise `None`).
  - `available` is shown only when it and `installed` both parse and `available > installed` as int tuples.
  - With `N` = `request` counter and `H` = status `request`: if `N > H`, state is `running`, unless the `request` file's mtime is more than 120 s old, which gives `failed` with "The updater inside Eggie did not start.".
  - Otherwise status `running` → `running`, unless `status.json`'s mtime is more than 1800 s old, which gives `failed` with "The update did not finish.".
  - `failed` → `failed` with its `error`.
  - Anything else (`done`, missing, malformed) → `idle`.
- `request()`:
  - `running` → `Conflict("update_running", "Eggie is already updating.")`
  - `available is None` → `Conflict("update_not_available", "This Eggie is already up to date.")`
  - otherwise bump `request` with the same temp-file + `os.replace` + `0o660` pattern as `AgentStatus._bump` (`services/agents.py`); `OSError` → `Unavailable("update_unavailable", "This Eggie can't update itself yet. Restart it to update.")`
  - return `status()`

- [ ] **Step 1: Write failing tests** (service-level with a tmp dir and an injected clock, plus one route test through the existing app-factory fixtures in `tests/runtime/api/conftest.py`):
  - `test_a_newer_release_is_offered_without_the_tag_prefix` → `{"installed": "0.1.0", "available": "0.2.0", "state": "idle", "error": None}`
  - `test_a_release_the_boot_update_already_installed_is_not_offered` (available `0.1.0`, installed `0.1.0`; and installed `0.10.0` vs available `0.9.0`) → `available is None`
  - `test_missing_or_malformed_files_mean_nothing_to_offer` → `idle`, `available is None`
  - `test_a_request_not_yet_picked_up_is_running`, then `…_older_than_two_minutes_has_failed`
  - `test_an_update_running_for_half_an_hour_has_failed`
  - `test_a_failed_update_carries_its_error`
  - `test_requesting_bumps_the_counter_once_and_reports_running` (`request` 4 → 5)
  - `test_requesting_while_running_is_refused` (409, `update_running`, counter unchanged)
  - `test_requesting_with_nothing_newer_is_refused` (409, `update_not_available`)
  - `test_the_routes_are_console_only`: the bearer `GET /runtime/update` → 404, and the cookie `GET /api/runtime/update` → 200. If `test_api_routes.py` or `route_sweep.py` lists every route, register the new ones there instead of writing this test.
- [ ] **Step 2: Run** `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_runtime_update.py -q` → FAIL.
- [ ] **Step 3: Implement** the service, router, config, constants, wiring and CLAUDE.md bullet.
- [ ] **Step 4: Run** the file, then `TMPDIR=/home/ihor/tmp python3 -m pytest -q` (layering, constants and route-sweep tests included) → all PASS.
- [ ] **Step 5: Commit** `feat(api): report and request a runtime update`

### Task 3: Console — header pill, panel and updating overlay

**Files:**
- Create: `runtime/web/apps/console/src/update/update.ts` (types, `updateView`, `useRuntimeUpdate`, `useStartUpdate`), `src/update/view.test.ts`, `src/shell/UpdateButton.tsx`, `src/screens/UpdatingOverlay.tsx`
- Modify: `src/App.tsx` (`trailing` = `<UpdateButton />` before `<AccountMenu>`), `src/shell/Shell.module.css` if the pill needs a style, `src/mocks/handlers.ts` (default: `available: null`; scenarios `update-available`, `update-failed`, `updating`)
- Modify: `runtime/web/CLAUDE.md` (one bullet: what decides the pill, the mock scenarios)

**Interfaces:**
- Consumes: Task 2's wire shape.
- Produces:
  - `type RuntimeUpdate = { installed: string | null; available: string | null; state: "idle" | "running" | "failed"; error: string | null }`
  - `type UpdateView = { kind: "hidden" } | { kind: "offer"; available: string; installed: string | null; problem: string | null } | { kind: "updating" } | { kind: "done" }`
  - `updateView(status: RuntimeUpdate | undefined, startedFrom: string | null): UpdateView`, rules in order:
    - `undefined` → `hidden`
    - `startedFrom !== null && status.state !== "running" && status.installed !== startedFrom` → `done`
    - `running` → `updating`
    - `available === null` → `hidden`
    - `failed` → `offer` with `problem = error ?? "no reason given"`
    - otherwise `offer` with `problem: null`
  - `useRuntimeUpdate()`: query key `["runtime-update"]`, `GET /api/runtime/update`, `refetchInterval` 3 000 ms while the view is `updating` (or a start is in flight), else 600 000 ms. `retry: false`. Fetch errors keep the last data; the API is down mid-update.
  - `useStartUpdate()`: mutation `POST /api/runtime/update`; on success, sets the query data.
- Behaviour:
  - `UpdateButton` renders nothing for `hidden`. For `offer`, it renders a pill button (icon `arrow-up` or the closest in `packages/ui/src/icons.ts`) labelled "Update available", which opens a popover panel. Follow `AccountMenu`'s open/close and outside-click pattern.
  - The panel reads: title "Eggie {available} is available"; line "You have {installed}." when known; "Eggie restarts for about a minute. Your projects keep running."; when `problem`: "The last update didn't finish: {problem}"; primary button "Update now", or "Try again" when `problem`.
  - The mutation's API error message shows inline.
  - When the user clicks, remember `startedFrom = installed`.
  - `updating` renders `UpdatingOverlay`, a fixed full-page `StatusScreen`-style layer with the title "Updating Eggie…" and the line "This takes about a minute. Your projects keep running."
  - `done` calls `window.location.reload()` once.

- [ ] **Step 1: Write failing tests** in `src/update/view.test.ts`:
  - `hidden while nothing newer exists`, and `hidden before the first answer`
  - `offers the available release with no problem`
  - `a failed attempt still offers, with its error` (and `"no reason given"` for `error: null`)
  - `a running update shows the overlay even when this tab did not start it`
  - `the update is done once the installed release moved from where it started`
  - `a failed update this tab started goes back to the offer, not done` (state `failed`, installed unchanged)
- [ ] **Step 2: Run** `cd runtime/web && npx vitest run apps/console/src/update/view.test.ts` → FAIL.
- [ ] **Step 3: Implement** `update.ts`, `UpdateButton.tsx`, `UpdatingOverlay.tsx`, the App wiring, mocks and the CLAUDE.md bullet.
- [ ] **Step 4: Run** `npx vitest run apps/console/src/update/view.test.ts`, `npm test`, `npm run typecheck`, `npm run build && npm run check-offline` → all PASS.
  - Optional visual check: `npm run dev` with `?scenario=update-available`, using headless Chrome + Playwright as in the repo's earlier console checks.
- [ ] **Step 5: Commit** `feat(console): offer a runtime update from the header`

### Task 4: Human docs

**Files:**
- Modify: `docs/releasing.md` (the "how installed VMs move" part: at boot, or from the console's Update button once the check has seen the release; checks every 6 h)
- Modify: `docs/site-mode.md` (the snapshot paragraph that says the boot update keeps droplets current: add the console offer)
- Modify: `docs/architecture.md` (update flow, if it describes it)
- Modify: `docs/release-testing.md`: **append** new cases after the last existing one and never renumber: (a) on a running VM a day after a release, the pill appears, Update now, overlay, reload, new version; (b) update with the network cut mid-pull shows "Try again" with the reason, and the old version keeps serving.

- [ ] **Step 1: Make the edits.** Keep them short and match each file's tone.
- [ ] **Step 2: Commit** `docs: runtime update from the console`
