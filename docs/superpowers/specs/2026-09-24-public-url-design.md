# Public URL for a project (VM side)

Date: 2026-09-24
Status: implemented (PR #24); service side agreed, not yet live
Issue: #23

## 1. Problem

A signed-in user wants to share a running project with someone outside their
computer. The Omelet service (`OMELET_CLOUD_URL`) runs the control plane on top of
Cloudflare Tunnel: it owns the tunnel, its routing config in Cloudflare, the public
hostnames and the limits. This spec is the part inside the VM: asking the service
for a public URL, running the tunnel client, and showing the result.

Everything here lives in `runtime/`. No host change and no host release.

## 2. Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | The VM is written against the service contract in section 3, agreed with the service on 2026-09-25. | The live API did not match on 2026-09-24; the service dev changed it to this. As with sign-in, no client-side workarounds for service gaps. |
| 2 | The service rewrites the Host header to the project's local hostname; project routing and overlays do not change. | Verified: project routers are `Host(<local hostname>)` with no entrypoint restriction (`core/overlay.py`), and Traefik v3's `Host()` ignores the port. |
| 3 | Apps that build absolute URLs from the Host header send public visitors to `*.127-0-0-1.sslip.io`. Accepted as a known limit (section 10). | Most dev apps use relative links. The fix (public-host routes via a Traefik dynamic file) is a follow-up. |
| 4 | Only the console turns a public URL on or off. The in-VM `omelet` CLI and coding agents never see public URLs. | Putting a project on the internet is a human decision. Agents only need local URLs. |
| 5 | The tunnel client is a `stack.yml` service behind a compose profile, started and stopped by the API. | Absent when unused, visible in the stack, and the API already runs compose. |
| 6 | The tunnel client sits on its own network, shared only with Traefik. | Cloudflare, not us, decides where the client sends traffic. A bad remote config must not reach the api container (it holds the Docker socket) or project containers directly. |
| 7 | The VM hardcodes no limits: no duration, no "one at a time" check. | Limits come from the service (`expires_at`, 409) and will vary by plan. |
| 8 | URLs are stored only while a public URL is on. | The URL changes every time; an old one is never shown or reused. |
| 9 | Local URLs keep working unchanged while a public URL is on. | The tunnel is an extra way into the same Traefik. |

## 3. Service contract (agreed with the service, 2026-09-25)

All calls use the device's account token (`Authorization: Bearer <device token>`)
through `Account.authed()`. `{id}` is the service project id already stored in
`cloud_projects` by sync.

### `POST /v1/tunnels/projects/{id}/url` — turn on

```json
request: {"origin": "http://traefik:39080",
          "routes": [{"local_hostname": "recipe-box.127-0-0-1.sslip.io", "service": "web"},
                     {"local_hostname": "api.recipe-box.127-0-0-1.sslip.io", "service": "api_v2"}]}

201:     {"id": "…", "project_id": "…", "slug": "k3x9m2p7qa", "created_at": "…",
          "expires_at": "2026-09-24T15:00:00Z",
          "url": "https://k3x9m2p7qa.omelet.app",
          "urls": [{"service": "web", "local_hostname": "recipe-box.127-0-0-1.sslip.io",
                    "url": "https://k3x9m2p7qa.omelet.app"},
                   {"service": "api_v2", "local_hostname": "api.recipe-box.127-0-0-1.sslip.io",
                    "url": "https://api-v2--k3x9m2p7qa.omelet.app"}],
          "credentials": {"provider": "cloudflare", "token": "…"}}
```

- `origin` is where the tunnel client sends traffic; only the VM knows it (the edge
  port is configurable). The service uses it as the ingress target for every route,
  with `originRequest.httpHostHeader = <local_hostname>`; without it the service
  falls back to `http://traefik:39080`.
- `routes`: 1–10, `local_hostname` required and unique; the main web service first.
  `service` accepts any Compose name and never fails the request.
- The service chooses every public domain: the first route gets
  `https://<slug>.<domain>`, the others `https://<name>--<slug>.<domain>`, where
  `<name>` is the service lowercased with `_`/`.` turned into `-` (or the route's
  position when missing or clashing). Every session gets a new slug; none is reused.
- `urls[]`: one item per route, in request order. The VM keys them by
  `local_hostname` and shows `url`.
- `expires_at: null` means no time limit (always set on the current plan).
- `credentials` is always present on 201. The token is stable per device; it changes
  only when the device is removed and paired again. The VM recreates the tunnel
  client only when the token differs from the one it holds.
- 200 instead of 201: this device already has an active URL for the project; same
  URLs, and `credentials` with the same token. Should a 200 ever omit `credentials`,
  the VM keeps the token it holds rather than releasing a live session.
- Errors, in the usual `{"error":{"code","message"}}` body:
  - `409 public_url_active` — the account already has an active public URL.
  - `403 public_url_unavailable` — the plan has no public URLs.
  - `403 device_required` — not called with a device token.
  - `404 project_not_found` — the service does not know the project.
  - `422 validation_error` — the routes or origin are invalid.
  - `502 tunnel_provider_error` — Cloudflare failed; retry later.
  - `503 public_urls_disabled` — public URLs are switched off on the server.

### `GET /v1/tunnels/projects/{id}/url`

200 with the shape above, or 404 when no URL is live. The VM only needs the status
code here; it never reads `credentials` from a GET.

### `DELETE /v1/tunnels/projects/{id}/url`

204, or 404 when there is nothing to release. The VM treats both as released.

### Service behaviour the VM relies on

- The service removes the routes itself within about a minute of `expires_at`,
  whether or not the VM is online. The VM's expiry handling is display and cleanup,
  never the security boundary.
- `DELETE /v1/projects/{id}` also releases that project's public URL (the backstop
  if the VM's own release call is lost).

### Status

Agreed on 2026-09-25; the service will announce when it is live. The OpenAPI
does not yet declare `origin`, `urls[].local_hostname`, the error codes or a
`bearerAuth` scheme — the VM is written to the agreed contract above.

## 4. Stack and installer

### `runtime/stack.yml`

```yaml
  tunnel:
    image: cloudflare/cloudflared:<pinned version>
    profiles: [tunnel]
    restart: unless-stopped
    command: tunnel --no-autoupdate run --token-file /run/omelet/token
    volumes:
      - /opt/omelet/tunnel:/run/omelet:ro
    group_add:
      - "${OMELET_DOCKER_GID:-999}"
    networks:
      - tunnel
```

- `traefik` joins a new compose-managed `tunnel` network. `api`, `web` and
  projects do not.
- The installer's profile-less `up -d` never starts `tunnel`, so without a token it
  is absent.
- `--token-file` keeps the token off the command line and out of the container's
  environment.
- The token's directory is mounted read-only, not the file: docker creates a missing
  file mount source as a root-owned directory, which would wedge every later write
  and removal of the token. A missing directory just mounts empty.
- The cloudflared image runs as a non-root user; `group_add` lets it read the 0640
  docker-group token, the same way `api` reads `api.token`.

### `runtime/install/install.sh`

`docker compose -f /opt/omelet/stack.yml pull` becomes `... --profile tunnel pull`,
so the first "turn on" never waits for an image download. `up -d` stays
profile-less. Nothing else changes: after a repair the API's startup reconcile
brings the client back if a URL is on.

### The API drives the client

Through `LocalRunner`, inside the api container, where `/opt/omelet/stack.yml` and
`/opt/omelet/.env` are visible at the same paths:

- on: `docker compose -f <stack_file> --profile tunnel up -d --no-deps tunnel`
- off: `docker compose -f <stack_file> --profile tunnel rm -sf tunnel`

The compose project name is derived from `/opt/omelet`, the same on both sides.

## 5. Runtime: `core/public.py`

Platform-free. Takes `account`, `cloud`, `state`, `runner`, the token path and the
stack file, plus injectable `clock` and `spawn` (the `Account` pattern).

### Config

`ApiConfig` gains `stack_file` (default `/opt/omelet/stack.yml`, env
`OMELET_STACK_FILE`) and `tunnel_token_path` (default `/opt/omelet/tunnel/token`,
env `OMELET_TUNNEL_TOKEN`). `origin` is `http://{traefik_host}:{edge_port}`, from
values that already exist.

### Storage

None in `state.db`. The service is the record of which URLs are on; the API keeps
in memory only what a restart may lose:

- the last record read per project (`cloud_id`, `urls`, `expires_at`);
- an in-flight turn-on, as an attempt object compared by identity, so a turn-on
  that a turn-off or sign-out dropped meanwhile undoes itself instead of committing;
- a failure reason, or an off note (`expired`, `released_elsewhere`,
  `client_failed`), to show until the next turn-on;
- releases that failed while the service was unreachable, retried each pass.

After a restart the first sync pass re-reads every project from the service, so a
URL that is still on is picked up and its client started again; until then it reads
as off. A failure shown before the restart is gone.

### Token file

`/opt/omelet/tunnel/token`, in a directory created on first write and mounted
read-only into the client: written to a temp file created with mode 0640 in the
same directory, then renamed over; group `docker` via `/opt/omelet`'s setgid bit.
It exists only while a URL is on. Turning off, expiry, sign-out and delete remove it.

### Turn on

1. Refuse at once, as `unavailable`, when: not signed in (`signed_out`); the project
   has no `cloud_projects` mapping yet (`not_registered`); the project has no web
   service (`no_web`). A project already turning on answers 409 `project_busy`, and so
   does turning it off meanwhile. A URL already on is returned as is, unless its
   `expires_at` has passed: then a new one is made.
2. Record the attempt, return `enabling`, and do the rest on a spawned thread.
3. `POST` with one route per web service (`host_for` for its local hostname, the
   primary first) and `origin`.
4. On 201/200: write the token and start the client (recreated only when the token
   changed), then keep the record, if the attempt is still current. If it is not,
   stop the client unless another project needs it and `DELETE` on the service.
5. If the client does not start, or the reply is malformed: stop it, delete the
   token, `DELETE` on the service, fail with `client_failed`.
6. On a service error or `CloudUnavailable`: fail with the code (section 6).
   `NotSignedIn` mid-call: nothing is kept; the status reads `signed_out`.

### Turn off

Stop the client and delete the token (unless another project's URL needs them),
then `DELETE` on the service. Locally the URL is off at once: with the client
stopped the address no longer answers. A failed `DELETE` is retried on the next pass,
since the service still counts it against the one-per-account limit.

### Reconcile

At the start of every sync pass (the first runs at API startup), when signed in:

- Retry the pending releases.
- `GET` every mapped project that still exists. A record replaces what is kept,
  unless a turn-on or turn-off landed during the call. A 404 drops what is kept with
  an off note: `expired` if its `expires_at` had passed, else `released_elsewhere`.
  `CloudUnavailable` ends the pass: nothing is concluded, the client is left alone.
- Unless a turn-on is in flight: no unexpired URL kept → stop the client and delete
  the token. One kept but the client is not running → start it; with no token file,
  drop it as `client_failed` and `DELETE` it on the service.
- A failing reconcile is logged and never stops the rest of the sync pass.

This costs one `GET` per mapped project per pass. A single "this device's active
URL" endpoint on the service would make it one call; not there yet.

Status reads compare `expires_at` with the clock and report `off` with the
`expired` note the moment it passes, before reconcile has cleaned up.

### Project delete

`DELETE /projects/{id}` turns the public URL off before the existing removal and `sync.wake()`. Section 3's release-on-project-delete
is the backstop.

### Sign-out and account change

`Account.sign_out()` turns every live URL off while the token is still valid, before
forgetting it. `_forget` (sign-out, revoked) stops the client, deletes the token and
clears everything kept in memory. A revoked account cannot call the service; the service's own
expiry covers it.

## 6. API routes and states

`GET` is on the shared router (`/api/...` for the console, `/...` behind the bearer
token). `POST` and `DELETE` are console-only: mounted at `/api/...` alone, so the
guest token the CLI and coding agents hold cannot turn a public URL on or off.
Additive: `API_VERSION` does not change and the host never calls them.

- `GET /projects/{id}/public` — the status.
- `POST /projects/{id}/public` — 202 with `enabling`. A precondition failure answers
  409 with the unavailable reason's code and message; a project already `enabling`
  answers 409 `project_busy`.
- `DELETE /projects/{id}/public` — the `off` status.
- The project payload (list and single) gains `public` with the same status. The
  in-VM CLI does not print it.

```json
{"state": "unavailable", "reason": {"code": "…", "message": "…"}}
{"state": "off", "note": null}
{"state": "off", "note": {"code": "expired", "message": "…"}}
{"state": "enabling"}
{"state": "on", "urls": [{"url": "https://…", "service": "web", "local_url": "http://…"}],
 "expires_at": 1790000000}          // null: no time limit
{"state": "failed", "reason": {"code": "…", "message": "…"}}
```

`unavailable` is computed on each read (account and mapping), never kept.

### Wording

The API owns these sentences so every surface says the same thing. A service error
code not listed here reads "The Omelet service refused: <its message>".

| code | shown as | message |
|---|---|---|
| `signed_out` | unavailable | Public addresses need an Omelet account. Sign in to use them. |
| `not_registered` | unavailable | This project isn't linked to your account yet. Try again in a minute. |
| `no_web` | unavailable | This project has no web page to share. |
| `public_url_active` | failed | Only one public address can be on at a time. Turn off the one on "<project>" first. — or, when none is on in this VM: One is already on for another project or computer. Turn it off there first. |
| `public_url_unavailable` | failed | Your plan doesn't include public addresses. |
| `cloud_unavailable` | failed | The Omelet service couldn't be reached. Check the internet connection and try again. |
| `client_failed` | failed | The public connection couldn't start on this computer. |
| `expired` | off note | The public address expired. Start a new one; it will be a different address. |
| `released_elsewhere` | off note | The public address was turned off from the Omelet website. |
| `project_not_found` | failed | This project isn't linked to your account yet. Try again in a minute. |
| `device_required` | failed | This computer's sign-in can't make public addresses. Sign out and sign in again. |
| `tunnel_provider_error` | failed | The public address couldn't be set up. Try again in a few minutes. |
| `public_urls_disabled` | failed | Public addresses are switched off on the Omelet service right now. |
| `validation_error` | failed | The Omelet service couldn't accept this project's addresses. |

## 7. Console

- `projects/public.ts` — `publicView(status, now)`: the API's `public` field plus the
  time to what screens show. An `on` whose `expires_at` has passed becomes `off` with
  the `expired` note. Time left is formatted from `expires_at` only; a null
  `expires_at` shows "On" with no countdown.
- `screens/project/Tiles.tsx` — the Public address tile becomes live. Its small line:
  Off / Turning on… / 42 min left / Didn't work, or the unavailable reason with the
  tile disabled. It opens `PublicModal`.
- `screens/project/PublicModal.tsx`:
  - off — "Anyone with the link can open <project> until it expires. You get a new
    address each time." The ended note above it, if any. **Make it public**.
  - enabling — "Turning on…"; the project query polls while in this state.
  - on — each public URL with Copy and Open (`openExternal`), the time left,
    **Turn off**. When the project is stopped: "Start the project so visitors can
    see it."
  - failed — the reason, **Try again**.
- `screens/project/AddressRows.tsx` — while on, each address row gains a second
  line: `Public · <host> · Copy`.
- `screens/list/ProjectRow.tsx` — a "Public" marker with time left on the project
  that is on.
- Mocks — `?scenario=public-on|public-expiring|public-active-elsewhere|public-unavailable`;
  `public-expiring` ends 20 s after load.

## 8. Release

A runtime release: `__version__`, the Dockerfile's `SERVICE_VERSION` and
`stack.yml`'s api and web tags go to 0.3.0 together. `API_VERSION` and `get.sh`
are unchanged. No host change.

## 9. Testing

Only where a wrong result is plausible:

- `core/public.py`, against a fake cloud, a fake runner, a fake clock and a temp
  token path:
  - a successful enable writes a 0640 token, starts the client, stores the URLs;
  - `public_url_active` names a project that is on in this VM and falls back to the
    "elsewhere" wording otherwise;
  - a client that does not start is released on the service;
  - turning off while the service is unreachable is off here at once, and the next
    reconcile retries the release;
  - reconcile: an expired URL ends (client stopped, token gone); a `GET` 404 ends as
    `released_elsewhere`; a URL the service still has after a restart is picked up;
    an unreachable service leaves the client running; a 404 read before a fresh
    turn-on does not drop it;
  - status reports `off`/`expired` once the clock passes `expires_at`, before
    reconcile runs.
- Routes: deleting a project and signing out both release a live URL.
- `core/cloud.py` seam: the POST body is `{origin, routes}`; the real error body
  maps to the codes above.
- `stack.yml` boundary guard: `tunnel` is profile-gated, reads a token file, is not
  on `edge`; `api` is not on `tunnel`.
- `install.sh` text assertion: the pull uses `--profile tunnel`.
- Console: `publicView` flips to off at expiry, formats time left, and maps each
  status to its view.

Not tested: the enable thread and event wiring, the modal's rendering (glue).

### Manual live checks

- Re-running the installer leaves a running `tunnel` container alone.
- cloudflared reads the 0640 token.
- End to end, once the service ships section 3.

## 10. Out of scope and known limits

- **Absolute URLs.** An app that builds links or redirects from its Host header
  (Django `build_absolute_uri`, Rails `url_for`, WordPress site URL, OAuth
  callbacks, emailed links) sends public visitors to `*.127-0-0-1.sslip.io`, which is
  their own machine. Follow-up: the service passes the public host through and the
  API writes Traefik dynamic-file routes for it.
- Residual reach: the tunnel client can reach Traefik's edge entrypoint with any
  Host the service's config sets, including the console's. The console's API still
  needs its session cookie and Origin check, so this exposes only the static page.
  The `tunnel` network also reaches the VM through its gateway, so every port
  published on 0.0.0.0 (the api's, and any `ports:` a user project publishes) is
  reachable from the tunnel client.
- The service accepts at most 10 routes per URL; a project with more web services
  gets `validation_error` ("The Omelet service couldn't accept this project's
  addresses.").
- A CLI command for public URLs, host changes, and the service changes themselves.
