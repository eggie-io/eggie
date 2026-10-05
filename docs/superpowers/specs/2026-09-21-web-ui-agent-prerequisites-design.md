# Eggie web UI — decisions, and part A: agent prerequisites

Date: 2026-09-21
Design board: `Eggie Web UI.dc.html` (claude.ai/design project `5a77e605`);
snapshot and frame-to-part map in `docs/design/`

The web UI is the page at `http://localhost:39080` a non-technical user manages
projects from. The board has sixteen frames drawn at the desktop window size
(880×620) plus one at 1200 px, and a strip of repeating parts. Sync with the
Service Layer is out of scope; so is creating a project from GitHub.

## 1. Decisions that bind all four parts

**Split.** Four spec → plan → PR cycles, in order:

| Part | Items | Contents |
|---|---|---|
| A | 18–22 | Agent routes the UI cannot work without; host "Open Eggie" handoff. This document. |
| B | 1–3 | `web/` workspace, UI kit from scratch, the `eggie-web` image, app shell at 880 and 1200 px, sign-in handoff and the "lost track of you" screen. |
| C | 4–9, 13–17 | Projects list, discovered band, create, detail, starting, something's wrong, Analyze, public address, delete, sync marker. |
| D | 10–12 | Files, upload destination, resumable queue, out-of-space states. |

**Stack.** React + TypeScript + Vite, as an npm workspace in top-level `web/`:
`web/packages/ui` (the kit: tokens, fonts, components) and `web/apps/console`
(the app). The kit is built from the board, from scratch; nothing is lifted from
`host/desktop/ui`, and the desktop does not consume the kit. The agent is moving
to its own repository, so the kit is shared later from there, not across this
repo's host/guest line.

**Serving.** A separate `eggie-web` image (nginx, static build only, no secrets,
no logic). It is released under the same `engine-vX.Y.Z` tag as the agent and
pinned next to it in `engine/stack.yml`, so the two always install as a pair;
`tests/test_constants_agree.py` gains the web tag. On load the app checks
`/api/health`'s `api` number and shows a plain "needs an update" state on a
mismatch.

**Scope cuts, agreed.**

- Uploads stop when the page closes. Copy that says "uploads carry on if you
  close this page" is rewritten to "pick up where it stopped when you're back".
- No `eggie://` URL scheme. "Open the Eggie app" becomes an instruction to
  switch to the desktop app, not a link.
- The sync marker is built with both variants but hidden until sync exists.
- The delete confirmation lists real container and volume names, not
  descriptions such as "orders, users, the lot".
- Out-of-space states show real numbers and point at the desktop app; freeing
  space there is future desktop work.

## 2. Serving and routing (part A's share)

Traefik on the edge port routes, for `Host(localhost) || Host(127.0.0.1)`:

- `PathPrefix(/api)` → the agent, at a priority above the catch-all.
- everything else → `web` (added in part B, with the image it points at, so
  part A never ships a route to a container that does not exist).

Part A adds only the agent's labels to `engine/stack.yml`. Project hosts
(`*.<domain>`) are untouched.

The agent keeps every existing route on `:39099` with the bearer token, for the
host and the in-VM CLI. The same handlers are mounted a second time under
`/api/` with session-cookie auth: one `APIRouter`, included twice with a
different auth dependency. No handler is duplicated.

## 3. Sessions and the allowlist (item 22)

**Handoff.**

1. The desktop's "Open Eggie" calls `POST /sessions/handoff` (bearer) and gets
   a one-time code: 32 random bytes, 60-second lifetime, held in memory only.
2. The host opens `http://localhost:39080/#handoff=<code>`. The fragment never
   reaches a server log or a `Referer`.
3. The page posts it to `POST /api/session`. The agent consumes the code and
   sets `eggie_session` — `HttpOnly`, `SameSite=Strict`, `Path=/api`, no
   `Secure` (plain http on loopback).
4. Sessions live in sqlite as a SHA-256 of the id, so they survive an agent
   restart. Seven days, sliding on use: once less than `SESSION_TTL - 1h`
   remains, a request extends the row and the middleware re-sends the cookie
   with a fresh `Max-Age` — at most one sqlite write an hour, not one per poll.
   `DELETE /api/session` signs out.

`GET /api/session` answers 200 or 401, so the page can decide what to render
before it asks for anything else.

**Allowlist, on every `/api/*` request.**

- `Host` must be `localhost:<edge>` or `127.0.0.1:<edge>`, else 403
  `forbidden_host`. This stops DNS rebinding, and stops the browser replaying
  the cookie at `localhost:39099` — cookies ignore the port.
- Any method other than GET/HEAD must carry an `Origin` from the same list,
  else 403 `forbidden_origin`. Project apps on `*.<domain>` are a different
  origin and are refused here.

The bearer mount does not read the cookie at all, and the cookie mount does not
accept a bearer token.

**Errors the UI renders.** 401 `not_signed_in` and `session_expired` → the
"lost track of you" screen. Code consumed or expired → 401 `handoff_invalid`.

**Host.** `host/desktop/api.py`'s "Open Eggie" asks `AgentClient` for a handoff
code, then opens the URL. If the agent predates the route (404), it opens
`http://localhost:39080` bare, as today. The route is additive: `API_VERSION`
stays 1, `SUPPORTED_API` is unchanged.

## 4. Projects the UI can rely on (items 18, 20, 21)

**Reconcile.** `GET /projects` also lists `projects_root` — one `scandir`, no
parsing beyond a file-exists check — and returns
`discovered: [{name, seen_at, adoptable, reason}]` for folders with no row.
Skipped: dot-folders and upload staging. `reason` is `compose_missing`, or
`bad_name` when `_slug(name) != name`. `seen_at` is the folder's mtime.

`POST /projects/{id}/adopt` registers an existing folder without starting it;
409 `not_adoptable` with the reason otherwise.

The reverse case: a row whose folder is gone carries problem `folder_missing`.
The UI's only action for it is "Forget it", which is the delete below.

**Payload additions.**

- `web: [{url, service, primary}]` — the detail page's "Where to find it"
  rows, labelled by service name.
- `job: {id, kind, phase, started_at} | null` — the project's running job, so a
  reopened page resumes the Starting screen.
- `first_run` — true until the project has once reached `started_ok`
  (a new `last_started_at` column).
- `empty` — the folder has no `docker-compose.yml` yet. A project created from
  scratch starts this way; the UI shows it as "waiting for your coding agent",
  not as something wrong, even though `problem` still carries
  `compose_missing` for the CLI.

**Job phases.** Jobs gain `phase`, reported through the existing
`GET /jobs/{id}`. An `up` job goes `preparing` (overlay) → `starting`
(`compose up`, which is where images are pulled or built) → `checking` (the
readiness probe). The UI's progress indicator and "1:12 so far" come from
polling phase and `started_at`, never from log output.

`POST /projects/{id}/restart` is a job: down, then the same steps as `up`.

**Delete that survives a broken project.**

- `GET /projects/{id}/delete-preview` →
  `{files, bytes, containers: [...], volumes: [...]}`, exactly what the
  confirmation lists.
- `DELETE /projects/{id}` stays synchronous with today's response shape
  (`{id, stopped, detail}`): the host CLI's `destroy`, install verification and
  the desktop's replace-import all call it and wait. It never reads the compose
  file any more: containers are stopped, then containers and networks are found
  by `label=com.docker.compose.project=<name>` and removed, so a broken
  `docker-compose.yml` no longer blocks it. Every step runs even if an earlier
  one failed; the first failure is what `detail` reports.
- `?purge=true` also removes the project's volumes and its folder. The web UI
  always sends it; existing callers do not, so their behaviour (forget the
  project, keep its files and volumes) is unchanged.
- `<name>` is resolved in order: the `com.docker.compose.project` label of any
  container whose `com.docker.compose.project.working_dir` is the project
  folder; then the `compose_name` column, recorded before every `up` (the
  directory name unless the file sets a top-level `name:`); then the id.

**Free space.** `GET /disk` → `{free_bytes, total_bytes}` via `statvfs` on
`projects_root`. A write that hits ENOSPC anywhere in the file routes answers
507 `disk_full` instead of `internal_error`.

## 5. Resumable chunked upload (item 19)

A cut-down tus: the server's only state per upload is how many bytes it holds.

| Route | Does |
|---|---|
| `POST /projects/{id}/uploads` `{path, size, fingerprint, replace}` | Starts one. Returns `{upload_id, offset: 0, chunk_size}`. |
| `PATCH /uploads/{upload_id}` + `Upload-Offset` header, chunk as body | Appends. Returns the new offset. |
| `GET /uploads/{upload_id}` | Current offset, size, path. |
| `DELETE /uploads/{upload_id}` | Cancels and removes the partial file. |
| `GET /projects/{id}/uploads` | Unfinished uploads, for "pick up where it stopped". |

- `path` is validated with `resolve_within`. Missing parent folders ("New
  folder" in the dialog) are created on completion, not before.
- Pre-check at start: `size + 1 GiB reserve > free_bytes` → 507
  `not_enough_space` with `free_bytes`. An existing target → 409 `file_exists`
  unless `replace`.
- `Upload-Offset` not equal to the stored offset → 409 `offset_mismatch`
  carrying the real offset; the client resumes from it.
- ENOSPC mid-chunk: the file is truncated back to the offset before the chunk,
  507 `disk_full` with that offset. The same PATCH works once there is room.
- Completion (offset reaches size): `os.replace` into the project, under the
  project lock for that step only — never for the transfer.
- Staging lives at `/opt/eggie/uploads/<upload_id>/` (`data` + `meta.json`),
  outside `projects_root`: a partial file never appears in a listing, in the
  reconcile scan, or to the coding agent. Uploads untouched for seven days are
  swept at agent start and on each list.
- `chunk_size` is 8 MiB. The existing 512 MB cap stays on the tar.gz and PUT
  routes the host and CLI use; chunked uploads are bounded by disk space.
- A browser cannot reopen a file after a reload. The UI asks for it again and
  matches it against the stored `fingerprint` (name, size, lastModified) before
  resuming from the server's offset.

**Browsing.** `GET /projects/{id}/files?dir=<path>` returns one level:
`[{name, kind, size, items, modified}]`. Without `dir` the route keeps today's
whole-tree answer. Downloads use the existing file route with
`Content-Disposition: attachment`.

## 6. Testing

Each test names the bug it catches.

| Test | The bug |
|---|---|
| Cookie request with `Host: localhost:39099` or a foreign host → 403 | Cookie replayed across ports; DNS rebinding |
| POST with `Origin` of a project app → 403 | CSRF from a user project |
| Bearer route ignores a valid cookie; cookie route rejects a bearer | The two auth modes leak into each other |
| Handoff code works once; expired code → `handoff_invalid` | Replayable handoff |
| Session outlives a second `create_app` over the same db | Every agent restart signs the user out |
| Expired session → `session_expired` | UI shows the wrong screen |
| Unregistered folder → `discovered`; dot and staging folders absent | Reconcile shows junk or misses projects |
| Folder without compose → `adoptable: false, reason: compose_missing` | Adopting something that cannot run |
| Row whose folder was removed → `folder_missing` | Listing crashes or hides the project |
| Delete with unparseable compose builds label argv, never `-f` | Broken project cannot be deleted |
| `purge=true` removes folder, volumes and row; without it the folder stays | Files left behind; existing callers lose data |
| Preview counts match a fixture tree | The confirmation lies |
| Offset mismatch returns the real offset | Client corrupts the file on resume |
| Injected ENOSPC truncates to the pre-chunk offset | Resume appends after a torn chunk |
| Completed upload lands atomically; partial never in listing | Coding agent reads a half-written file |
| `..` and absolute upload paths refused | Upload escapes the project |
| Pre-check refuses a file larger than free space minus reserve | Upload starts and fills the disk |
| Upload older than seven days swept | Staging grows forever |
| `up` job reports preparing → starting → checking in order | Progress indicator stuck or backwards |

Left untested on purpose: nginx and Traefik label syntax (covered by the
live-VM acceptance run), and `statvfs` itself.

## 7. Release

Agent version bumps together with the Dockerfile's `AGENT_VERSION` and
`engine/stack.yml` as usual. `API_VERSION` stays 1: every change adds a route or
a field. New sqlite columns (`last_started_at`, `compose_name`) and the
`sessions` table arrive as one migration step.

## 8. Follow-ups for parts B–D

What part A's reviews left for the parts that consume it. Each part still gets
its own spec; this is the input, not the design.

**B — shell and kit (items 1–3).**

- `web/` npm workspace: `packages/ui` (kit from the board — light/dark tokens,
  fonts bundled locally since the page must work offline, buttons, state badge,
  sync marker, prompt card, modal, row card, progress bar, collapsible) and
  `apps/console`.
- `eggie-web` nginx image with an `index.html` fallback; `web` service in
  `engine/stack.yml` with a Traefik catch-all for `Host(localhost) ||
  Host(127.0.0.1)` below the agent's `/api` router (priority 1000); the web tag
  joins `tests/test_constants_agree.py` and the release steps in `CLAUDE.md`.
- Boot order: read `#handoff=` → `POST /api/session` → clear it with
  `history.replaceState`; otherwise `GET /api/session`. 401 `not_signed_in` /
  `session_expired` → screen 15, with an instruction to open the desktop app,
  not a link. `/api/health` `api` mismatch → "needs an update"; no answer →
  "service isn't answering".
- Every non-GET request must send the page's own `Origin` (browsers do for
  `fetch`; don't strip it with a proxy in dev).

**C — project screens (items 4–9, 13–17).**

- List state is derived from `status`, `problem`, `job` and `empty` together:
  running job → Starting; `empty` → "waiting for your coding agent", not
  Something's wrong; `problem` → Something's wrong; else `status`.
- Starting screen polls `GET /jobs/{id}` for `phase` (`preparing` → `starting`
  → `checking`; down jobs report `stopping`) and `started_at`; `first_run`
  shows the slow-first-start note.
- `folder_missing` offers only "Forget it" (plain `DELETE`).
- Delete confirmation reads `GET /projects/{id}/delete-preview` and deletes
  with `?purge=true`. The preview can undercount folders the agent cannot read,
  and `stopped: false` can come back even when `docker rm -f` removed the
  containers after a failed stop — word the result as "may still be running",
  not as a failure.
- Delete can miss containers of a compose project that was renamed, or started
  by hand from `~/projects/<id>` (different `working_dir` label).
- Sync marker: built, hidden until sync exists.

**D — files and uploads (items 10–12).**

- Browse with `GET /projects/{id}/files?dir=`; entries the agent cannot stat
  are skipped; a folder it cannot open answers 409 `permission_denied`.
- Upload client: one file at a time, `chunk_size` from the start response.
  On `offset_mismatch`, continue from the `offset` in the error body.
- The final chunk can answer 409 `project_busy` while an `up` holds the project
  lock; retry with an empty `PATCH` at `Upload-Offset: <size>` until it
  finishes.
- Start errors to handle: `not_enough_space` (507, `free_bytes`; screen 13,
  pre-checked with `GET /disk`), `file_exists` (ask, then resend with
  `replace: true`), `path_is_folder`, `path_traversal`. Mid-transfer:
  `disk_full` (507, `offset`; screen 14). At finish: `permission_denied`,
  `project_not_found` (the upload is cancelled).
- After a reload: `GET /projects/{id}/uploads`, ask for the same file again,
  match `fingerprint` (`name:size:lastModified`), resume from the server offset.
- Copy must not promise uploads continue with the page closed.

**Outside the web UI.**

- The desktop's replace-import deletes without `purge`, so "replace" still
  merges into the old folder; it should pass `purge=true`.
- Known agent gaps left as is: sign-out that crosses the hourly cookie refresh
  leaves a dead cookie; expired session rows are only pruned when presented;
  ENOSPC while staging an upload's metadata answers 500 rather than 507;
  tree-mode `GET /projects/{id}/files` still 500s on an unreadable folder;
  `first_run` is true for projects started before agent 0.2.0.
