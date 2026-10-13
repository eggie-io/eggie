# Remote instance: the runtime on a service-provisioned server

Date: 2026-10-13
Status: design agreed, not implemented
Issue: eggie-io/eggie-resources#73

## 1. Problem

Today the runtime only runs in a VM on the user's own computer, reached over loopback
forwards from the desktop app. We want the same runtime on a server the Eggie service
provisions for the user (a DigitalOcean droplet to start), with the user opening its
console from the service's web application and landing there signed in.

This spec covers the runtime side ("site mode") and the contract the instance needs
from the service. The service side — creating the droplet, DNS, the certificate, minting
sign-in codes, SSH keys — is the service developer's; section 3 is written for them.

Everything here lives in `runtime/`. No host change, no host release, no desktop change.

## 2. Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | Instances are service-provisioned only. No "install on my own box and sign in" path in this version. | The service already has the account, DNS zone and Cloudflare credentials; the user never touches the server. |
| 2 | Direct access over DNS + TLS on the server, not the Cloudflare tunnel. Console at `foo.eggie.space`, projects at `<name>.foo.eggie.space`. | A tunnel needs a route per project and Cloudflare's free certificate stops one label below `eggie.space`. Two DNS rows per instance (`foo`, `*.foo`) cover every project forever. |
| 3 | DNS rows are DNS-only (grey cloud). TLS terminates in Traefik on the server with a Let's Encrypt wildcard. | No plan change at Cloudflare; Let's Encrypt wildcards are free at any depth. Gives up the proxy's DDoS shielding, accepted at this stage. |
| 4 | One certificate per instance, `foo.eggie.space` + `*.foo.eggie.space`, issued by the **service** via DNS-01 and fetched by the instance. | Let's Encrypt allows 50 new certificates a week per registered domain (`eggie.space`, across all customers); per-host issue would exhaust it. DNS-01 needs a Cloudflare token, which must not live on customer servers. |
| 5 | Every project host is one label under the domain: `web-bar.<domain>`, not `web.bar.<domain>`. Applies locally too. | So the single wildcard matches all project hosts. One hostname shape in both modes. |
| 6 | Site mode is switched on by one installer variable, `EGGIE_SITE_HOST`. The instance pulls everything else (certificate, identity) from the service once signed in. | Smallest cloud-init payload; first issue and renewal of the certificate are one code path. |
| 7 | The instance boots signed in: the service seeds the account token pair through cloud-init. | Reuses the existing account, refresh and sync code unchanged. |
| 8 | The browser signs in with a service-minted handoff code that the instance redeems **outbound** with its account token. | Matches how the VM already talks to the service (`infra/cloud.py`); no inbound credential, no second auth scheme on the instance (`docs/security.md`). The console's `#handoff` boot path is reused as is. |
| 9 | The bearer API port is bound to loopback on an instance. | Nothing outside the server may reach `/` routes; the guest CLI dials loopback and keeps working. |
| 10 | The public URL (tunnel) feature is refused in site mode; the tunnel profile is never started. | Every project on an instance already has a public address. |
| 11 | The instance switcher (console and desktop) is deferred. | Not needed to "install on a server and connect"; designed once remote instances are in use. |
| 12 | Project `ports:` published by compose stay published; the service-managed firewall blocks them. | Same stance as the public URL spec. Stated in `docs/security.md`, not fixed here. |

## 3. Service contract (for the service developer)

This section is self-contained. Everything the instance needs from the service, and
what the service must do before and after creating a server.

### 3.1 Provisioning a server

1. Create the droplet (Ubuntu 24.04, x86_64 or arm64; the runtime images are
   multi-arch). Attach a cloud firewall allowing inbound **22, 80, 443** only. Nothing
   else must be reachable: the runtime publishes other ports on all interfaces and
   relies on this firewall.
2. Choose the instance hostname, e.g. `foo.eggie.space`. Create two **DNS-only**
   records in Cloudflare pointing at the droplet's IP: `foo` and `*.foo`.
3. Issue a Let's Encrypt certificate for `foo.eggie.space` and `*.foo.eggie.space`
   (DNS-01 through the Cloudflare API). Keep it; the instance fetches it (3.3) and the
   service renews it (3.5).
4. Mint a **device** for the owner's account for this instance, with a device id and a
   token pair, the same objects the device-code flow issues to a desktop VM today.
5. Create the login account and put the user's SSH key in its `authorized_keys`. The
   runtime provisions every login account that exists at install time (docker group,
   project ACLs, agent files, skills), so do this **before** running the installer.
6. Pass the following cloud-init user data:

```yaml
#cloud-config
write_files:
  - path: /opt/eggie/account.json
    permissions: "0600"
    owner: root:root
    content: |
      {"device_id": "…", "access_token": "…", "refresh_token": "…", "expires_in": 3600}
runcmd:
  - cd / && curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/main/runtime/install/get.sh
      | EGGIE_SITE_HOST=foo.eggie.space bash
```

`cd /` matters: the installer runs per-account steps as that account, and root's home
is not enterable by other users. `EGGIE_RUNTIME_REF=<tag>` may be added to pin a
release; without it the newest `runtime-v*` tag is installed.

Record the instance (hostname, device id, droplet id, owner) on the service; the
instance list in the web app and the handoff check (3.4) read from it.

### 3.2 Authentication of instance calls

Every call below carries `Authorization: Bearer <device access token>`, the token seeded
in 3.1 and refreshed through the existing `POST /v1/auth/token/refresh`. The service
resolves the instance from the device id behind the token. An instance never holds any
other credential.

### 3.3 `GET /v1/instances/me/certificate` — fetch the certificate

```json
200: {"hostname": "foo.eggie.space",
      "certificate": "-----BEGIN CERTIFICATE-----\n…(full chain)…",
      "private_key": "-----BEGIN PRIVATE KEY-----\n…",
      "not_after": "2027-01-11T10:00:00Z"}
404: {"error": {"code": "no_certificate", "message": "…"}}   // not issued yet
```

The instance calls this on start when it has no certificate files, then once a day. It
replaces its files only when `not_after` is later than the one it holds. A 404 or any
failure leaves the current files in place.

### 3.4 `POST /v1/instances/handoff/redeem` — sign a browser in

```json
request: {"code": "k3x9m2p7qa…"}
200:     {"ok": true}
401/403/404/409: {"error": {"code": "handoff_invalid", "message": "…"}}
```

The web app's "open console" button mints a **single-use** code bound to the owner and
to one instance, with a short lifetime (a minute is plenty), then redirects the browser to
`https://foo.eggie.space/#handoff=<code>`. The instance's API receives the code from the
page and calls this endpoint; `200` means "this code was minted for this instance's
owner and has not been used", and the instance creates a browser session. Anything
else is shown to the user as "open the console from the web app again".

Codes must not be reusable, must not be valid on another instance, and must be
consumed on redemption even when the instance later fails to set a cookie.

### 3.5 Renewal and lifecycle

- Renew the certificate at least 30 days before `not_after`. The instance picks the new
  one up within a day (3.3).
- Deleting an instance: revoke its device tokens; the server itself is disposable.
- Signing out on the instance is hidden in its console but possible through the API; a
  signed-out instance cannot redeem codes or renew its certificate until re-seeded.

### 3.6 What the web app shows

Each instance: hostname and an "open console" action (3.4). Projects on an instance
are listed by the existing project registry sync under the instance's device id, so
nothing new is needed for them.

## 4. Provisioning as seen from the runtime

`get.sh` learns one variable, `EGGIE_SITE_HOST`, and persists it to
`/opt/eggie/site.env` next to `runtime.env`, so repairs, boot updates and manual reruns
keep it. `install.sh` sources `site.env` and:

- appends `EGGIE_SITE_HOST`, `EGGIE_DOMAIN=<site host>` and `EGGIE_API_BIND=127.0.0.1`
  to `/opt/eggie/.env` (written fresh on every run, as today);
- runs every compose command for the stack with
  `-f /opt/eggie/stack.yml -f /opt/eggie/runtime/stack.site.yml`;
- installs `/opt/eggie/tls/tls.yml`, the Traefik dynamic config naming
  `site.crt`/`site.key` as the default certificate, with the directory `0750 root:docker`
  so the API (docker group) can write the files;
- records `"vm": "site"` in `connect.json` alongside the first login account, as it
  records `wsl`/`lima` today.

Account seeding is the API's job, not the installer's (section 6.3).

## 5. The edge

### 5.1 `stack.yml` (identical in both modes, env-driven)

- Console and `/api` router rules become
  ``Host(`localhost`) || Host(`127.0.0.1`) || Host(`${EGGIE_SITE_HOST:-localhost}`)``.
- The api service's port mapping becomes
  `"${EGGIE_API_BIND:-0.0.0.0}:${EGGIE_API_PORT:-39099}:${EGGIE_API_PORT:-39099}"`.
  WSL2 keeps the all-interfaces default; site sets loopback.

### 5.2 `stack.site.yml` (override, next to `stack.debug.yml`)

Replaces Traefik's `command` and adds ports and a volume:

- entrypoint `web` on `:443` with `http.tls=true` — keeping the name `web` means every
  router label in the stack and in the project overlay stays as it is;
- entrypoint `http` on `:80` with a permanent redirect to `web`;
- `--providers.file.directory=/opt/eggie/tls --providers.file.watch=true`;
- the `readTimeout` and Docker provider flags copied from the base, because compose
  replaces `command` wholesale;
- `ports: ["80:80", "443:443"]` (appended to the base list; the base `39080` mapping
  then forwards to nothing and is firewalled anyway);
- `/opt/eggie/tls:/opt/eggie/tls:ro`.

Until the certificate files exist Traefik serves its default self-signed certificate;
the file watch reloads the moment the API writes them.

### 5.3 Hostnames

`domain/overlay.py::host_for` becomes `f"{sub}-{id}.{domain}"` for a service with a
subdomain, `f"{id}.{domain}"` otherwise. Locally the `*.local.eggie.space` wildcard
already resolves both shapes.

## 6. The API

### 6.1 Config

`ApiConfig.site_host: str | None` from `EGGIE_SITE_HOST`. Derived:

- `domain` is already `EGGIE_DOMAIN` (set to the site host by the installer);
- `public_url(host)`: `https://{host}` when `site_host` is set, else
  `http://{host}:{edge_port}` as today. `services/loader.py` uses it for `local_url`
  (field name kept; the console reads it unchanged).

### 6.2 Browser auth (`rest/auth.py`)

With `site_host` set, `allowed_hosts` also contains `site_host` and `allowed_origins`
also contains `https://{site_host}`. The session cookie gets `secure=True`. Nothing
changes locally.

### 6.3 Account seeding (`services/account.py`)

On start: if the account row has no refresh token and `/opt/eggie/account.json` exists
and parses, replace the row's `device_id` with the file's (no records exist yet that
could be orphaned), store the token pair and expiry, delete the file, run the identity
load. A malformed file is logged and left alone; an existing signed-in row ignores the
file. Site mode hides sign-out in the console (section 7) but the API keeps the route.

### 6.4 Handoff (`services/sessions.py`, `rest/routers/sessions.py`)

`POST /api/session` keeps redeeming local codes first. When that fails and the API is in
site mode, it calls `POST /v1/instances/handoff/redeem` through `Account.authed()`; a
`200` creates a session like a local redemption. Every other outcome, including the
instance not being signed in, is the existing `handoff_invalid` error.

### 6.5 Certificate (`services/certificate.py`, new)

Reads `/opt/eggie/tls/site.crt` on start; if missing, fetches now. A pass added to the
`SyncLoop` re-checks when the last check is older than a day, compares `not_after` with
the installed certificate's, and on a newer one writes `site.key` (0600) and `site.crt`
atomically (write to a temp name, rename) so Traefik never reads a half file. Failures
are logged and keep the current files. Only active in site mode.

### 6.6 Surface

- `GET /api/health` and `GET /health` gain `"site": "<hostname>" | null`.
- `GET /api/connect` (`infra/connect.py::facts`) returns `ssh: {host: <site host>,
  port: 22, user: <first login account>, key_file: null}` when `connect.json` says
  `"vm": "site"`; `"site"` joins the known kinds.
- `POST /projects/{id}/public` and the `/api` twin answer `409 not_available`
  ("every project on this server already has a public address") in site mode; `GET`
  reports `off`.

## 7. The console

Driven by `health.site`:

- the signed-out screen says to reopen the console from the Eggie web app, not from
  the desktop app;
- the public URL control is absent from the project row and page;
- the agents card shows the instance's SSH host and port 22 and no key file;
- sign-out is absent from the account menu.

Nothing else changes; project URLs come from the API.

## 8. Security

New rows for the boundary table in `docs/security.md`:

| Way in | What it reaches | Who may use it |
|---|---|---|
| 443 | Traefik: console, `/api`, every project host | Anyone on the internet; console and `/api` need a session from a service-minted handoff; projects have only their own auth |
| 80 | Redirect to 443 | Anyone |
| 22 | The login account | Holders of the SSH key the service installed |

The bearer API binds to loopback. Project `ports:` are published by compose and blocked
only by the service's firewall (decision 12). The certificate key is `0600 root` on
disk, written by the API through the docker group, mounted read-only into Traefik.
`account.json` is deleted after import; tokens live in `state.db` as today. The session
cookie is `Secure`, `HttpOnly`, `SameSite=Strict`, path `/api`, unchanged otherwise.

## 9. Testing

Only where a wrong answer is plausible:

- `test_overlay.py`: flattened hosts for a service with a subdomain.
- `test_api_browser_auth.py`: site host and `https` origin accepted, local still
  accepted, `Secure` on the cookie only in site mode.
- `test_sessions.py` / `test_api_auth.py`: service fallback on an unknown code, refusal
  mapped to `handoff_invalid`, no service call when not in site mode.
- `test_account.py`: seed imported once and file deleted; ignored when signed in;
  malformed file leaves the row untouched.
- new `test_certificate.py`: fetch on missing, daily re-check, replace only on a later
  `not_after`, atomic write, failure keeps files.
- loader: `local_url` is `https://<host>` without a port in site mode.
- `test_get_sh.py` / `test_install_shell.py` (real bash, fakes on PATH): `site.env`
  survives a repair, the compose commands carry the override only in site mode,
  `.env` carries the loopback bind, `tls.yml` installed.
- `test_stack_yml.py`: with `docker compose config` available, the merged site config
  publishes 80 and 443 and binds 39099 to loopback; skipped otherwise.
- console (Vitest): signed-out copy and public control depend on `health.site`.
- `docs/release-testing.md`: a manual "site" gate — provision, open console from the
  web app, start a project, reach it over `https`, reboot, confirm certificate reload.

## 10. Out of scope

Instance switcher (console and desktop), user-attached custom domains, Cloudflare
proxying, forward-auth in front of projects, desktop app changes, service-side
implementation beyond section 3, migration of existing local VMs (none exist in the
wild).

## 11. Open points

- The exact endpoint paths in section 3 are proposals; the service developer may
  rename them, the instance code is written against whatever is agreed.
- Whether the service wants a heartbeat from instances (e.g. runtime version) is a
  later addition to the sync pass.
