# Push a local project to a kitchen

Status: design, 2026-10-20. Service contract:
`eggie-web/docs/superpowers/specs/2026-10-20-kitchen-push-contract.md` (sibling repo), which already
includes the readiness probe, the `complete` failure rule and records that outlive local copies.
The service is not live: everything is built against fakes until it is.

Revised 2026-10-26 (PR #92 review): every push sends data. The "Move data" checkbox, the database
image list and `data_paths` are gone; a repeat push replaces files and volumes after one
confirmation. Code says "instance" where this text says kitchen (`instance_pushes`,
`InstanceClient`, `already_on_instance`, `EGGIE_INSTANCE_CA_FILE`, the service's `instances` and
`create_instance_url`); "kitchen" is only the word users see. Code updates without data are left to
a later git-based deploy.

## 1. Intent

A user picks a kitchen in the local console and pushes a project to it. The kitchen might become
production while the user keeps working locally, or the user might be moving to the cloud for
good. Either way, the kitchen copy is independent of the local one afterwards. Keep it as simple as
possible.

Project bytes go from the local VM straight to the kitchen over HTTPS. The service only mints a
single-use ticket that the kitchen redeems.

## 2. Goals, non-goals, anti-goals

Goals:
- One "Push to Kitchen" action per project (the wording isn't final), with a kitchen picker.
- A push sends the project's files and data. A repeat push replaces the kitchen's files and data,
  only after an explicit confirmation.
- Secret names travel; secret values never leave the machine. On the kitchen, a pushed project with
  unset secrets waits for them instead of starting broken.
- After the push, show the kitchen URL and a one-click way to the kitchen's secrets form.

Non-goals: resumable or incremental uploads, pulling from a kitchen, kitchen-to-kitchen, removing
a project from a kitchen through the service, pushing from the CLI, several orgs per user.

Anti-goals (a failure even if it works):
- A secret value in the bundle, a log or a URL.
- The transfer route accepting the API token or a console session, or a kitchen taking the
  project's identity from the bundle instead of the grant.
- Kitchen data overwritten without the user ticking the confirmation.
- A local delete taking the kitchen copy's service record with it.

## 3. Local console

### The action

A "Push to Kitchen" item in the project menu (`ProjectMenu.tsx`), placed after "Public address" and
shown only when `site === null`. Its states:

| state | how the console knows | item |
|---|---|---|
| no account linked | account status is not `signed_in` | disabled, note "Account required" (same pattern as Public address) |
| offline | `navigator.onLine === false`, or the targets call answers `cloud_unavailable` | disabled, note "Offline" |
| nothing to push | `project.empty` | disabled, note "Nothing to push yet" |
| a push is running | the project's active job has kind `push` | enabled, note "Pushing… 40%"; opens the dialog on its progress |
| otherwise | | enabled; the note names the last kitchen pushed to, if any |

### The dialog

It fetches `GET /api/projects/{id}/push` every time it opens (§4.1).

- **No kitchens:** "You don't have a kitchen yet" and a "Create a kitchen" button that opens
  `create_kitchen_url` in the system browser (`openExternal`). The desktop window never shows the
  web app.
- **A kitchen list:** one row per kitchen, showing the name, hostname and a status note. Only rows
  with `readiness === "ready"` and `is_owner` can be picked:
  - `starting` shows "Starting…", and the list is fetched again every 5 s while the dialog is open;
  - `failed` and `signed_out` show "Failed" / "Signed out" with a "Manage" link to `manage_url`;
  - `is_owner: false` shows "Owned by a teammate";
  - an unknown readiness value shows "Not available".
  A kitchen this project was already pushed to carries "Pushed <date>". The first pickable kitchen
  is selected. Under the list, a "Create a new kitchen" link opens `create_kitchen_url` in the
  system browser too.
- **Repeat push**: a warning: "<project> is already on <kitchen>. Pushing replaces its files and data
  there with what's on this computer. Changes made on the kitchen are lost." and a checkbox
  "Replace <project> on <kitchen>"; Push stays disabled until it is ticked. The choice resets when
  the selection changes.
- **Project running with volumes**: "<project> stops while its data is copied, then starts again."
- **Floating tag**: for each service with a floating tag (§4.3) that mounts a volume the push copies:
  "db uses postgres:latest. The kitchen may download a newer version that can't read this data. Pin
  a version, e.g. postgres:16." It does not block the push.
- **Progress**: after Push, the dialog shows the job, the same way `StartingBody` shows a start: a
  phase caption, elapsed time, and a progress bar showing bytes while sending. Closing the dialog
  doesn't cancel the push, and reopening the dialog shows the push still running.
- **Done**: "Pushed to <kitchen>" with the kitchen URL(s) and Open buttons. If secrets are missing
  there: a primary button "Enter N secrets on the kitchen", which opens
  `open_console_url + "&return_path=" + encodeURIComponent("/p/<kitchen project id>/secrets")` in
  the system browser. The last result is kept with the push record (§4.4), so reopening the dialog
  later still offers the button.
- **Failed**: the error message (§6), with "Try again" where it helps.

## 4. Local API

### 4.1 Routes (mounted at `/api` only, like Public address; the CLI never pushes)

`GET /api/projects/{id}/push`:

```json
{
  "kitchens": [ /* the service's targets rows, unchanged */ ],
  "create_kitchen_url": "https://app.eggie.io/kitchens/new",
  "pushed": { "<instance_id>": { "pushed_at": 1792510000, "kitchen_project": "my-shop",
                                 "urls": ["https://my-shop.k7x2m9p4qa.eggie.space"],
                                 "secrets_missing": 2 } },
  "floating": [ { "service": "db", "image": "postgres:latest" } ],
  "stops": true
}
```

Errors: `not_signed_in` (409), `cloud_unavailable` (503), and any other service error as
`cloud_error`.

`POST /api/projects/{id}/push` with body `{"instance_id": "…", "replace": true}` returns
`{"job_id": "…"}`. If the project was pushed to that kitchen before and `replace` isn't true, it
answers 409 `already_on_kitchen`. The local check drives the UI. The kitchen is the authority,
because the local record can be missing (§5.1).

### 4.2 The push job (`services/push.py`, job kind `push`)

The job holds the project lock from start to end, so Start and Stop answer `project_busy` until it
ends. `start_work` releases the lock in its own `finally`, so the push uses a variant that doesn't.
Steps and phases:

1. `preparing`: the project must be in `cloud_projects`. If it isn't, wake the sync loop and wait up
   to 15 s; then fail with `not_registered`.
2. `stopping` (the project is not stopped and has volumes to copy): `compose down`. Volumes that
   don't exist locally yet are left out (mounting one to pack it would create it).
3. `packing`: write the bundle (§4.3) to `/opt/eggie/transfers/out/<job>.tar`. A throwaway root
   container of the API's own image does the work (as `remove_tree_as_root` does today), so files
   owned by root are readable. Check local free disk first and fail with `disk_full`.
4. `starting` (only if step 2 stopped it): the normal start. If the start fails, the push goes on
   and the result says so.
5. `sending`: mint a ticket (`POST /v1/transfers/tickets`) and open the transfer on the kitchen
   (§5.1), then upload the bundle, reporting bytes sent. Write the `kitchen_pushes` row as soon as
   the kitchen accepts the upload. Retries, with a fresh ticket each time:
   - `transfer_ticket_invalid`: once.
   - `kitchen_unreachable`: up to three attempts in total.
   - `already_on_kitchen` from the kitchen: never. Write the
     `kitchen_pushes` row, so the dialog shows the repeat-push warning, and fail the job with that
     code.
6. `waiting`: poll the kitchen's status every 2 s and mirror its phase (`unpacking`,
   `restoring data`) until it is done or failed.
7. Record the push (§4.4). Delete the staging file in `finally`, and sweep
   `/opt/eggie/transfers/out` when the API starts.

The job's `result` is `{instance_id, kitchen, kitchen_project, urls, secrets_missing,
open_secrets_url, local_restart}`. The job dict gains `progress: {"done": n, "total": n}`.

### 4.3 The bundle

One uncompressed tar:

```
manifest.json
files/<project tree>
volumes/<compose volume key>/        # the volume's contents
```

`manifest.json`:

```json
{ "format": 1,
  "volumes": ["db-data"],
  "secrets": [ { "name": "STRIPE_KEY", "hint": "…" } ] }
```

- **Files**: the project folder minus `.git`, `node_modules`, `.venv`, `__pycache__` and
  `.eggie/overlay.yml`. These are the same sets the folder import and `host/client.py` use, with
  one Python definition on the runtime side. The project's `.env` goes in.
- **Data** is every named volume the compose file declares and a service mounts (`external: true`
  volumes are skipped and listed in the job log). A bind-mounted folder inside the project is a
  project file, whatever mounts it.
- **Floating tag**: no tag, or a tag with no digit (`latest`, `alpine`). A `@sha256:` digest is
  pinned.
- **Secrets**: the names of stored secrets, plus the outstanding requests with their hints. Never
  values.

### 4.4 What the local side remembers

New table `kitchen_pushes(local_id, instance_id, console_hostname, kitchen_project, pushed_at,
urls_json, secrets_missing)`, with primary key `(local_id, instance_id)`. It drives the "Pushed"
mark, the repeat-push warning and the confirmation. The rows
stay when the local project is deleted, so a recreated project with the same id still gets the
warning. The row is written when the kitchen accepts the upload, and again when the push ends.

### 4.5 Sync keeps a pushed project's record

`domain/sync.plan` takes the set of local ids that have a `kitchen_pushes` row. For those, a
missing local project becomes `Forget` instead of `Delete`, and the service record stays. If the
same device later recreates that project id, the idempotent `POST /v1/projects` with the same
`client_ref` returns the same record. That is intended.

### 4.6 Service client (`infra/cloud.py`)

Add `transfer_targets`, `mint_transfer_ticket`, `redeem_transfer_ticket` and `complete_transfer`.
`CloudError` gains `retry_after: int | None`, read from the 429 `Retry-After` header. The kitchen
HTTP client (`infra/kitchen.py`, `KitchenClient`) opens the transfer, sends the bundle as a
streamed body with `Content-Length`, and polls the status. Tests inject a fake. It always checks
TLS. Dev kitchens use Let's Encrypt staging certificates, so `EGGIE_KITCHEN_CA_FILE` can name an
extra CA bundle to trust, for example the staging roots. There is never a switch that turns
verification off.

## 5. Kitchen (site mode)

### 5.1 The transfer entry point

These routes are mounted only on a site, under `/api/transfers`. `rest/auth.py` gets its own
branch for them: `Host` must be `site_host`, there is no cookie or Origin check, and the API token
and a session are **not** accepted. The router authenticates every request itself.

There are two requests, so a refused ticket or a full disk is answered before any bytes are sent:

`POST /api/transfers`, with no body. Headers:
- `Eggie-Transfer-Ticket: <ticket>`;
- `Eggie-Transfer-Size: <bundle bytes>`;
- `Eggie-Transfer-Replace: none|all`. This is what the user confirmed: `none` for a first push,
  `all` after the repeat-push confirmation.

In order:
1. A limit of 10 attempts a minute per sender address (first `X-Forwarded-For` entry, else the
   socket peer) and 60 a minute in all: 429 `kitchen_busy`.
2. Redeem the ticket (`POST /v1/transfers/tickets/redeem`). On a failure, answer 403 with the
   service's `transfer_ticket_invalid` envelope unchanged. If the service is unreachable, answer
   503 `cloud_unavailable`.
3. Resolve the kitchen project from the grant (§5.2) and take its project lock. If it is held,
   answer 409 `kitchen_busy`.
4. **The kitchen enforces the overwrite rules.** If the project already exists here and Replace is
   `none`, answer 409 `already_on_kitchen`.
5. Free disk must be at least 2 × size + 1 GiB. Otherwise answer 507 `kitchen_disk_full`, with the
   needed and free bytes in the message.
6. Answer 201 `{"push_id", "token"}`. `token` is 32 random url-safe bytes; only its hash is kept, in
   memory, until one hour after the push ends. An opened push that receives no upload within
   10 minutes is dropped, and the lock is released.

On every refusal after step 3, release the lock.

`PUT /api/transfers/{push_id}/bundle` with `Eggie-Transfer-Token`. `Content-Length` must equal the
declared size. The kitchen streams the body to `/opt/eggie/transfers/in/<push_id>.tar`
(`stream_to_tempfile`, capped by `max_upload_bytes`), starts the import job and answers 202. A
failed upload ends the push, and a retry starts over with a fresh ticket.

`GET /api/transfers/{push_id}` with `Eggie-Transfer-Token` returns `{state, phase, detail, result}`.

Tokens are compared in constant time. An unknown id and a wrong token both get 404, and the two
look the same. The ticket and the token never appear in a URL or a log line.

### 5.2 Which kitchen project

New table `pushed_projects(local_id PRIMARY KEY, cloud_project_id UNIQUE, source_device_id,
first_pushed_at, last_pushed_at)`. If a row has the grant's `project.id`, replace that project.
Otherwise create a new local project named from `_slug(project.name)`, adding `-2`, `-3`, … when
the id is taken. Nothing in the bundle names the project.

The `pushed_projects` row is written when the project is resolved at `POST /api/transfers`, before
any file lands and before `complete`. So a copy left behind by a failed push is still a pushed
project, and the next push replaces it instead of creating `-2`.

The kitchen's own sync loop leaves out every local id in `pushed_projects`, so it never registers
or deletes a pushed project. A copy whose service record was deleted in the web app stays as it is,
unlinked, as the contract says. Deleting a pushed project on the kitchen removes only its
`pushed_projects` row; nothing is sent to the service.

### 5.3 The import job (job kind `import`, holds the project lock)

1. `unpacking`: extract into `/opt/eggie/transfers/in/<push_id>/` with `extract_archive` (data
   filter plus the absolute-path check). Check the manifest: format 1, secret names pass
   `check_name`, and the volume keys exist in the pushed compose file.
2. Stop the kitchen project if it is running.
3. Replace the files. Move the old folder aside and move the new tree in, then remove the old
   folder (root helper container). If the move fails, put the old folder back.
4. `restoring data` (volumes in the bundle): for each volume, remove the existing `<compose name>_<key>`
   volume, create it, and untar into it through the root helper container.
5. Merge secret names (§5.4).
6. Call `POST /v1/transfers/{ticket_id}/complete`, retrying three times on `CloudUnavailable`.
   Any error that persists fails the job and keeps the files. `project_not_found` says "This
   project was deleted from your account during the push". On success, update
   `pushed_projects.last_pushed_at`.
7. Result: `{kitchen_project, urls, secrets_missing}`. With no secrets missing, submit an ordinary
   start once the lock is released. Otherwise set `start_when_ready` (§5.5) and leave the project
   stopped.

API restart: jobs are in memory, so a poll then answers 404, and the local side says "The kitchen
restarted during the push. Push again." The `transfers/in` folder is swept when the API starts.

### 5.4 Secret names

The pushed names are P, the kitchen's stored values S, and its open requests R.

| case | result |
|---|---|
| in P and S | value kept; the `unused` mark cleared |
| in P, not in S | a request with the pushed hint, or "Set on your computer. Enter its value for the kitchen." |
| in S, not in P | value kept, marked unused (new column `secrets.unused_at`) |
| in R, not in P | request removed (no value to keep) |

The secrets form lists unused names under "Not used by the latest push", each with Delete. An unused
value is still passed to compose; the mark is only a label.

### 5.5 Waiting for secrets

This applies to every project, local and kitchen, so there is one rule:

- A project's card shows a "Needs N secrets" badge while `secrets_requested > 0`.
- Start on a project with requests opens its secrets form instead, and calls
  `POST /api/projects/{id}/start-when-ready`, which sets `projects.start_when_ready`. The form shows
  "Starts once these are set" and a "Start without them" button, which is the ordinary start.
- When `SecretService.put` stores the last requested name and `start_when_ready` is set, the flag
  is cleared and the project starts. This goes through a callback that `wiring.py` passes in, so no
  service-to-service cycle appears. Any start or stop clears the flag.

### 5.6 Sign-in landing on a page

`SiteSignIn.redeem` returns `(session_id, return_path)` from the service's redeem answer.
`POST /api/session` answers `{"return_path": …}`. The API checks it again: it must start with `/`,
the second character can't be `/` or `\`, and it has no `\`, `#`, whitespace or control
characters, otherwise it is `null`. After sign-in, the console's boot runs the first two checks
again and calls `history.replaceState` to that path before the router mounts.

### 5.7 Readiness

`GET /api/ready`, on a site only, open (added to `open_browser_routes`): 200 `{"ready": true}` when
the account is signed in and Docker answers; otherwise 503 `not_ready`. A release that serves it
also serves `/api/transfers`.

## 6. Errors the console shows

| code (from) | message |
|---|---|
| `not_signed_in` | Push needs an Eggie account. Sign in to use it. |
| `cloud_unavailable` | The Eggie service couldn't be reached. Check the internet connection. |
| `not_registered`, `project_not_found` (mint) | This project isn't linked to your account yet. Try again in a minute. |
| `instance_not_running` (mint) | <kitchen> isn't ready. Pick it again once it is running. |
| `instance_forbidden` (mint) | Only the kitchen's owner can push to it. |
| `instance_not_found` (mint) | That kitchen is gone. (the list is fetched again) |
| `transfer_rate_limited` (mint) | Too many pushes. Try again in N minutes. (from `Retry-After`) |
| `transfer_ticket_invalid` (kitchen, after the one retry) | The kitchen didn't accept the push. Try again. |
| `kitchen_disk_full` | <kitchen> doesn't have room: it needs X GB and has Y GB free. Free space there and push again. |
| `kitchen_busy` | <kitchen> is busy with this project. Try again in a minute. |
| `already_on_kitchen` (local or kitchen) | <project> is already on <kitchen>. Confirm that it should be replaced, then push again. (the dialog switches to the repeat-push confirmation) |
| `kitchen_unreachable` (connect or TLS failure, or a kitchen 404 on `/api/transfers`) | <kitchen> isn't answering, or runs an older Eggie. Try again in a minute. |
| `disk_full` (local) | This computer doesn't have room to pack the project. |

## 7. Mocks and tests

- `fake_cloud.py` gains the four transfer methods. A fake kitchen client stands in on the local
  side, and a fake cloud on the kitchen side. No test touches the network or a real VM.
- Console MSW scenarios: `push-no-account`, `push-offline`, `push-no-kitchens`, `push-kitchens`
  (mixed readiness, one pushed before), `push-running`, `push-done-secrets`, `push-rate-limited`,
  `kitchen-waiting-secrets`.
- A live run waits for the service.
- Tests that earn their keep: which volumes are data and the floating-tag rule; bundle contents
  (exclusions, bind folders as files, volumes, no secret values); the secret-name merge table; the
  kitchen replace rolling back on failure;
  `transfer_ticket_invalid` retried exactly once; sync `Forget` for pushed projects; the kitchen's
  sync skipping pushed projects; `return_path` validation on both sides; the transfer route refusing
  the API token, a session and a wrong Host, and never reading the body when the ticket fails;
  `start_when_ready` firing on the last secret only.

## 8. Docs

- `security.md`: the transfer entry point (its own auth, ticket in a header, rate limit, redeem
  before reading, safe unpack, Host check), what leaves the machine (files including `.env`,
  volumes, secret names, never values), and the push token.
- `site-mode.md`: the transfers endpoints the kitchen calls, `/api/transfers`, `/api/ready`,
  `return_path`, pushed projects kept out of sync.
- `release-testing.md`: new cases with fresh IDs (S-series, appended), covering a first push with
  data, a repeat push (confirmation required, files and data replaced), unset secrets leading to waiting then a start on save, the secrets link landing on the
  form, and a local delete after a push (the web app still lists the project).
- `runtime/eggie_api/CLAUDE.md`: a "Push" feature-area entry.

## 9. Release order

The service probes `/api/ready` before it marks a kitchen `running`, so a runtime release with
`/api/ready` and `/api/transfers` ships before the service turns the probe on.

## 10. Left to the builder

The exact phase captions and copy (the wording isn't final), compression (the kitchen opens the
bundle with `r:*` either way), the local free-disk estimate, the root helper container's exact
commands, and the dialog layout within the existing `@eggie/ui` kit.

Known limits: one push must finish within Traefik's 30 min `readTimeout` on the kitchen. A
database kept in a project folder travels as files and loses its owner and 0700 mode, which
Postgres refuses; data belongs in a named volume.
