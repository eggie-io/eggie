# Account sign-in and project registry sync

Date: 2026-09-23
Status: proposed

## 1. Problem

Eggie has no notion of a user. Before someone works with the projects console
they must be signed in to the Eggie service (`https://app.eggie.io/api`,
OpenAPI at `/api/openapi.json`), and every project on a device must exist as a
record on that service so the web app can list it.

Two things are asked for:

1. **Sign-in by URL or QR code.** The console shows a short code, a link and a QR
   code; the user approves on any browser or phone; the console unlocks.
2. **Project registry sync.** Each local project has a service record. Creating a
   project locally creates the record; deleting it locally deletes the record.

## 2. Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | Sync scope is the **registry only**: identity and existence. No status, no URLs, no files. | Smallest thing the web app can build on. |
| 2 | **Only the UIs are locked** — the web console and the desktop window's view of it. The in-VM `eggie` CLI and coding agents work without an account. | An agent must never stall on a sign-in nobody is watching. |
| 3 | Projects made before sign-in are pushed on the first pass after sign-in. | Follows from 2. |
| 4 | Sign-in and sync live **in the runtime** (`runtime/eggie_api`). The token is stored in the VM. The host is not touched. | Host/runtime split: a change inside the VM must never need a host release. The same `get.sh` provisions a cloud VM, which gets sign-in for free. |
| 5 | Sync is a **reconcile loop**, not an outbox or inline route calls. | One mechanism covers offline, not-yet-signed-in, crashes and pre-sign-in projects. |
| 6 | **Projects are per device.** Two devices never share a record, even with equal names. Moving a project to another device goes through GitHub and a fresh setup. | Product decision. |
| 7 | Local projects cannot be renamed (the id is the slug), so sync sends **create and delete only**. | Nothing to rename. |
| 8 | Once signed in, the UI stays unlocked while the service is unreachable. | The token is cached; sync catches up. |
| 9 | Both plain and `purge` local deletes delete the service record. | The local project is gone either way. |
| 10 | **No client-side workarounds for service gaps.** The runtime is written against the API as it should be (section 7); a gap shows up as a failure, not as a patch in our code. | Workarounds outlive the gaps they cover. |

## 3. Service API assessment (as probed on 2026-09-23)

**Sign-in is complete** — an RFC 8628 device authorization grant:
`POST /v1/auth/device/code` → `device_code, user_code, verification_uri,
verification_uri_complete, expires_in (600), interval (5)`;
`POST /v1/auth/device/token` answers 400 `authorization_pending` / `slow_down` /
`invalid_grant` until approved, then `access_token, refresh_token, expires_in`;
`POST /v1/auth/token/refresh`, `POST /v1/auth/logout`, `GET /v1/identity/me`,
`GET /v1/auth/devices`, `DELETE /v1/auth/devices/{id}`. Error bodies are
`{"error":{"code","message"}}`; 401 is `not_authenticated` or `invalid_token`.

**Project sync is not complete**: `GET/POST /v1/projects`, `GET /v1/projects/{id}`
only. `ProjectIn` is `{name}`. `PATCH` and `DELETE` answer 405.

The required service changes are listed in section 7.

## 4. Runtime: account

### Components

- `runtime/eggie_api/core/cloud.py` — the service client. Stdlib `urllib` only,
  the one place that knows `/v1/...` paths. Turns every non-2xx
  `{"error":{code,message}}` into `CloudError(code, message, status)` and a
  refused or timed-out connection into `CloudUnavailable`.
- `runtime/eggie_api/core/account.py` — platform-free account logic: the
  device-code state machine, token storage, refresh.
- `ApiConfig.cloud_url` — from `EGGIE_CLOUD_URL`, default
  `https://app.eggie.io/api`. The only place the service address comes from.

### Storage

Migration `_v4_account` adds to `state.db` (already under `/opt/eggie`, readable by the
docker group — root-equivalent already):

```
account(id INTEGER PRIMARY KEY CHECK (id = 1),
        device_id TEXT NOT NULL,          -- uuid4, generated once per runtime install
        email TEXT, org_id TEXT,
        access_token TEXT, refresh_token TEXT, access_expires_at REAL,
        device_code TEXT, user_code TEXT, verification_url TEXT, code_expires_at REAL,
        poll_interval REAL, sync_ok_at REAL, sync_error TEXT,
        last_error TEXT)
cloud_projects(local_id TEXT PRIMARY KEY, cloud_id TEXT NOT NULL, org_id TEXT NOT NULL)
```

`device_id` identifies this runtime install (this VM). Reinstalling the VM makes a
new device; its old records are orphaned on the service (section 8).

### Local routes

On the shared router, so the console reaches them at `/api/...` behind its cookie
and the host/CLI at `/...` behind the bearer token:

- `GET /account` →
  `{"state":"signed_out","error":<code|null>}` |
  `{"state":"pending","user_code","url","expires_at"}` |
  `{"state":"signed_in","email","sync":{"last_ok_at","last_error"}}`
- `POST /account/sign-in` — starts a device flow, or returns the pending one while
  it is still valid. Starts a background poller that waits `interval` between
  polls and adds 5 s on every `slow_down`, as RFC 8628 requires. On approval it
  stores the tokens, reads `GET /v1/identity/me` for `email` and `current_org_id`,
  clears the device-code fields and wakes the sync loop. On `access_denied`,
  `expired_token` or `invalid_grant` it returns to `signed_out` with that code in
  `error`. Answers 503 `cloud_unavailable` when the service can't be reached.
- `POST /account/sign-out` — calls `POST /v1/auth/logout`; clears tokens,
  `email`, `org_id` and `cloud_projects` even if that call fails. `device_id`
  stays.

These routes are additive: `API_VERSION` does not change, and the host never
calls them.

### Tokens

Every authenticated service call goes through one helper in `account.py`:

- refresh first when `access_expires_at` is less than 60 s away;
- on a 401 `invalid_token`, refresh once and retry once;
- a refresh answered `invalid_grant` (device revoked) → `signed_out` with
  `error: "revoked"`;
- a refresh that fails with `CloudUnavailable` leaves the account signed in
  (decision 8);
- account writes that follow a network call re-check the stored token/code under
  one lock, so a sign-out or a new code landing mid-call is never undone.

Whatever refresh returns replaces both stored tokens.

## 5. Web console: the lock and the sign-in screen

### Boot

`boot.ts` already says "signed in" for the *local console session* (the handoff
cookie). The account state is a separate word: after the session check passes,
boot reads `GET /api/account` and returns `{kind: "needsAccount"}` for anything
but `signed_in`. `App.tsx` renders the sign-in screen for it. The desktop window
shows this same console, so it is locked with no host change. The desktop's own
local screens (install, VM status) are never locked.

### `screens/account/SignIn.tsx`

- **Signed out** — "Sign in to Eggie" → `POST /api/account/sign-in`. If
  `error` is set, a one-line reason above the button (denied, expired, revoked).
- **Pending** — the `user_code` large, a QR code of `url`, an "Open sign-in page"
  link, a countdown to `expires_at`. Polls `GET /api/account` every 3 s; on
  `signed_in` it goes to the project list without a reload.
- **Service unreachable** (503 `cloud_unavailable`) — says the Eggie service
  can't be reached, with Retry. The existing `notAnswering` screen stays reserved
  for the local API.

`url` is shown only when its scheme is `https:`; anything else renders "The sign-in
link from the Eggie service is not valid" and no QR code. This is input
validation on an address from outside, not a rewrite: the page never substitutes
another address.

### QR code

`qrcode-generator` (MIT, no dependencies) rendered to inline SVG in
`components/Qr.tsx`. The QR only encodes the external address; the page never
loads it, so the CSP and `check-offline` are unaffected.

### The external link

A plain `<a href target="_blank" rel="noopener noreferrer">`. A browser opens a
tab; the desktop window hands new windows to `web_links_only`, which opens the
system browser for http(s) only.

### Sign out

The shell's menu shows the signed-in email and "Sign out"
(`POST /api/account/sign-out`), which returns to the sign-in screen.

### Mocks

`mocks/handlers.ts` gains `?scenario=account-needed|account-pending|account-denied|account-unreachable`.
Mock sign-in URLs are `https:`.

## 6. Runtime: the sync loop

### Where it runs

- `core/sync.py` — `plan(local_ids, mapping, org_id) -> list[Action]`, pure,
  plus `apply(actions, cloud, state)`. `eggie-selftest` (the install smoke
  test) is never sent.
- A daemon thread started by `routes/__main__.py`, not by `create_app()`, so
  importing the app or building it in a test starts no thread. It runs one pass
  every 60 s and at once when a `threading.Event` is set by project create,
  adopt and delete, by sign-in, and at startup.

A pass runs only when signed in. All calls go through the token helper.

### One pass

1. **Org changed** — mappings whose `org_id` differs from the account's are
   dropped (a different account signed in; its records are not ours to delete).
2. **Local project with no mapping** — `POST /v1/projects` with
   `{"name": local_id, "client_ref": "<device_id>/<local_id>"}`; store the
   returned `id`. The service must return the existing record for a repeated
   `client_ref` (section 7, S3); that makes a retry after a lost reply safe.
3. **Mapping whose local project is gone** — `DELETE /v1/projects/{cloud_id}`;
   204 or 404 drops the mapping. Any other answer is a failure and the mapping
   stays for the next pass.
4. **Service projects with no mapping** are never touched: they belong to other
   devices or to the web app.

A failure is per project: it is recorded in `sync.last_error` and never stops the
rest of the pass. A pass that finishes without failures sets `sync.last_ok_at`.
There is no special case for any status code beyond 204/404 on delete.

"Local projects" are the rows in `projects`. Coding agents' `eggie up/new/clone`
go through the API and create rows; a folder that was never adopted is not a
project yet.

## 7. Service changes required

These are service work items; the runtime does not work around any of them.

| # | Change | Blocks |
|---|--------|--------|
| S1 | `verification_uri` / `verification_uri_complete` must be the public sign-in page. Today they are `http://localhost:5174/device`. | Sign-in: the link and QR code are unusable; the console refuses the non-https address. |
| S2 | `DELETE /v1/projects/{id}` → 204, 404 when already gone. Today 405. | Delete sync: every local delete stays a failed mapping retried forever. |
| S3 | `ProjectIn.client_ref` (optional string, unique per org); a repeated `POST` with the same `client_ref` returns the existing record (200). | Safe create retries; without it a lost reply leaves a duplicate record. |
| S4 | `client_ref` (or a `device_id`) on `ProjectOut`, and `GET /v1/projects?client_ref_prefix=` or `?device_id=`. | The web app grouping projects by device. |
| S5 | `TokenOut` or `/v1/identity/me` returns the service's `device_id` for a device token. | Tying our `device_id` to the service's device record, so revoking a device in the web app can act on its projects. |
| S6 | OpenAPI documents the 400 device-token errors (`authorization_pending`, `slow_down`, `access_denied`, `expired_token`, `invalid_grant`), the 401s (`not_authenticated`, `invalid_token`) and the `{"error":{code,message}}` body; declares a `bearerAuth` security scheme; states whether `/token/refresh` rotates the refresh token. | Nothing at runtime; `access_denied` and `expired_token` are assumed from RFC 8628 and unverified. |

## 8. Out of scope and known gaps

- Public URLs (`/v1/tunnels/...`), organization switching (we use
  `current_org_id`), project status, URLs and files.
- Any host or desktop change.
- A VM reinstall orphans the old device's records; cleaning them up is a web-app
  concern once S4/S5 exist.
- The console does not yet show `sync.last_error`.

## 9. Testing

Written to the repo's testing rules — only where a wrong result is plausible:

- `core/sync.py` `plan()` — table-driven: create for an unmapped project; delete
  for a mapping with no local project; foreign-org mappings dropped; unmapped
  service projects ignored.
- `apply()` against a fake `cloud`: delete 204 and 404 drop the mapping, 500 keeps
  it; one project's failure does not stop the others; `last_ok_at` only on a
  clean pass.
- `core/account.py` — the poller's handling of `authorization_pending`,
  `slow_down` (interval grows), `access_denied`/`expired_token` (back to
  `signed_out`); refresh on near-expiry and on 401 once; `invalid_grant` on
  refresh → `revoked`; `CloudUnavailable` on refresh keeps the account signed in.
- `core/cloud.py` — the real error body shape becomes `CloudError(code, ...)`; a
  refused connection becomes `CloudUnavailable`. Driven by an injected opener;
  no network.
- Console — `boot()` returns `needsAccount` for `signed_out` and `pending`;
  the sign-in screen refuses a non-`https:` url.
- The migration: a v3 database migrates to v4 with its projects intact.

Not tested: the daemon thread and the event wiring (glue), `Qr.tsx` (the library
is the logic).
