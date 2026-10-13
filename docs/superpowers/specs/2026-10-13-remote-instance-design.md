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
from the service. The service side — the snapshot, creating the droplet, DNS, the
certificate, minting sign-in codes, SSH keys — is the service developer's; section 3 is
written for them.

Everything here lives in `runtime/`. No host change, no host release, no desktop change.

## 2. Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | Instances are service-provisioned only, from a **snapshot** that already holds the installed runtime. No "install on my own box and sign in" path in this version. | The service already has the account, DNS zones and credentials; the user never touches the server. A snapshot turns first boot into configuration, not installation. |
| 2 | Direct access over DNS + TLS on the server, not the Cloudflare tunnel. | A tunnel needs a route per project and Cloudflare's free certificate stops one label below the zone. Two DNS rows per instance cover every project forever. |
| 3 | Console on `foo.eggie.io`, projects on `*.foo.eggie.space`. Control plane and user apps on different registered domains. | A project is arbitrary code and a subdomain can set cookies for its parent; on a separate domain it cannot touch the console's origin. Same split as github.com / github.io. |
| 4 | Projects are `bar.foo.eggie.space`, nested under the instance, never flat `bar.eggie.space`. | Flat names need a DNS row per project (droplets have different IPs) and a `*.eggie.space` certificate shared by every server. |
| 5 | DNS rows are DNS-only (grey cloud at Cloudflare). TLS terminates in Traefik on the server. | No plan change at Cloudflare; Let's Encrypt is free at any depth. Gives up the proxy's DDoS shielding, accepted at this stage. |
| 6 | **One certificate per instance**, three names: `foo.eggie.io`, `foo.eggie.space`, `*.foo.eggie.space`. Issued by the **service** via DNS-01 (Route53 + Cloudflare), fetched by the instance. Never a zone-wide wildcard on a server. | A `*.eggie.io` key on every customer server could impersonate `app.eggie.io`. Per-host issue would hit Let's Encrypt's 50 certificates/week/registered domain; one per instance stays within it. DNS tokens must not live on customer servers. |
| 7 | Every project host is one label under the project domain: `web-bar.<domain>`, not `web.bar.<domain>`. Applies locally too. | So the single wildcard matches all project hosts. One hostname shape in both modes. |
| 8 | Site mode is switched on by two installer variables, `EGGIE_SITE_HOST` (console) and `EGGIE_SITE_DOMAIN` (projects). The instance pulls everything else (certificate, identity) from the service once signed in. | Smallest cloud-init payload; first issue and renewal of the certificate are one code path. |
| 9 | The instance boots signed in: the service seeds the account token pair through cloud-init. | Reuses the existing account, refresh and sync code unchanged. |
| 10 | The browser signs in with a service-minted handoff code that the instance redeems **outbound** with its account token. | Matches how the VM already talks to the service (`infra/cloud.py`); no inbound credential, no second auth scheme on the instance (`docs/security.md`). The console's `#handoff` boot path is reused as is. |
| 11 | The bearer API port is bound to loopback on an instance. | Nothing outside the server may reach `/` routes; the guest CLI dials loopback and keeps working. |
| 12 | The public URL (tunnel) feature is refused in site mode; the tunnel profile is never started. | Every project on an instance already has a public address. |
| 13 | The instance switcher (console and desktop) is deferred. | Not needed to "install on a server and connect"; designed once remote instances are in use. |
| 14 | Project `ports:` published by compose stay published; the service-managed firewall blocks them. | Same stance as the public URL spec. Stated in `docs/security.md`, not fixed here. |

## 3. Service contract (for the service developer)

This section is self-contained: everything the instance needs from the service, and
what the service must do before and after creating a server.

### 3.1 Building the snapshot (once per runtime release you want baked in)

1. Create a droplet from Ubuntu 24.04 (x86_64 or arm64; the runtime images are
   multi-arch).
2. As root, from `/`:

```bash
cd / && curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/main/runtime/install/get.sh | bash
```

   No site variables: this installs Docker, pulls the images, installs the stack. Add
   `EGGIE_RUNTIME_REF=<tag>` to pin a release.
3. Scrub per-instance state and cloud-init so the image is reusable:

```bash
bash /opt/eggie/runtime/install/snapshot-prep.sh
```

   It stops the stack and removes the API token, `state.db`, `connect.json`,
   `site.env`, the `tls` files and cloud-init's instance state. Then power off and take
   the snapshot.

A snapshot does not need rebuilding on every runtime release: the boot update unit
moves each instance to the newest release on its first boot. Rebuild when first boot
gets slow.

### 3.2 Provisioning a server

1. Create the droplet from the snapshot. Attach a cloud firewall allowing inbound
   **22, 80, 443** only. Nothing else must be reachable: the runtime publishes other
   ports on all interfaces and relies on this firewall.
2. Choose the instance name, e.g. `foo`. Create DNS records pointing at the droplet's
   IP: `foo.eggie.io` in Route53; `foo` and `*.foo` under `eggie.space` in Cloudflare,
   **DNS-only** (grey cloud).
3. Issue one Let's Encrypt certificate for `foo.eggie.io`, `foo.eggie.space` and
   `*.foo.eggie.space` (DNS-01 on both zones). Keep it; the instance fetches it (3.4)
   and the service renews it (3.6).
4. Mint a **device** for the owner's account for this instance, with a device id and a
   token pair, the same objects the device-code flow issues to a desktop VM today.
5. Pass the following cloud-init user data. The login account must exist before the
   installer runs: the runtime provisions every login account present at that moment
   (docker group, project ACLs, agent files, skills).

```yaml
#cloud-config
users:
  - name: ihor
    shell: /bin/bash
    groups: [sudo]
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - ssh-ed25519 AAAA… the user's key
write_files:
  - path: /opt/eggie/account.json
    permissions: "0600"
    owner: root:root
    content: |
      {"device_id": "…", "access_token": "…", "refresh_token": "…", "expires_in": 3600}
runcmd:
  - cd / && curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/main/runtime/install/get.sh
      | EGGIE_RUNTIME_REPAIR=1 EGGIE_SITE_HOST=foo.eggie.io EGGIE_SITE_DOMAIN=foo.eggie.space bash
```

`EGGIE_RUNTIME_REPAIR=1` keeps the release the snapshot holds and only configures: no
apt, no image pulls. `cd /` matters: the installer runs per-account steps as that
account, and root's home is not enterable by other users. Expect the stack to answer
on 443 within a minute or two of boot (skills for the login account install from npm).

Record the instance (name, hostnames, device id, droplet id, owner) on the service;
the instance list in the web app and the handoff check (3.5) read from it.

### 3.3 Authentication of instance calls

Every call below carries `Authorization: Bearer <device access token>`, the token
seeded in 3.2 and refreshed through the existing `POST /v1/auth/token/refresh`. The
service resolves the instance from the device id behind the token. An instance never
holds any other credential.

### 3.4 `GET /v1/instances/me/certificate` — fetch the certificate

```json
200: {"names": ["foo.eggie.io", "foo.eggie.space", "*.foo.eggie.space"],
      "certificate": "-----BEGIN CERTIFICATE-----\n…(full chain)…",
      "private_key": "-----BEGIN PRIVATE KEY-----\n…",
      "not_after": "2027-01-11T10:00:00Z"}
404: {"error": {"code": "no_certificate", "message": "…"}}   // not issued yet
```

The instance calls this on start when it has no certificate files, then once a day. It
replaces its files only when `not_after` is later than the one it holds. A 404 or any
failure leaves the current files in place.

### 3.5 `POST /v1/instances/handoff/redeem` — sign a browser in

```json
request: {"code": "k3x9m2p7qa…"}
200:     {"ok": true}
401/403/404/409: {"error": {"code": "handoff_invalid", "message": "…"}}
```

The web app's "open console" button mints a **single-use** code bound to the owner and
to one instance, with a short lifetime (a minute is plenty), then redirects the browser
to `https://foo.eggie.io/#handoff=<code>`. The instance's API receives the code from
the page and calls this endpoint; `200` means "this code was minted for this instance's
owner and has not been used", and the instance creates a browser session. Anything
else is shown to the user as "open the console from the web app again".

Codes must not be reusable, must not be valid on another instance, and must be
consumed on redemption even when the instance later fails to set a cookie.

### 3.6 Renewal and lifecycle

- Renew the certificate at least 30 days before `not_after`. The instance picks the new
  one up within a day (3.4).
- Deleting an instance: revoke its device tokens; the server itself is disposable.
- Signing out on the instance is hidden in its console but possible through the API; a
  signed-out instance cannot redeem codes or renew its certificate until re-seeded.

### 3.7 What the web app shows

Each instance: its console hostname and an "open console" action (3.5). Projects on an
instance are listed by the existing project registry sync under the instance's device
id, so nothing new is needed for them.

## 4. Provisioning as seen from the runtime

`get.sh` learns `EGGIE_SITE_HOST` and `EGGIE_SITE_DOMAIN` and persists them to
`/opt/eggie/site.env` next to `runtime.env`, so repairs, boot updates and manual reruns
keep them. Both or neither must be set. `install.sh` sources `site.env` and, in site
mode:

- appends `EGGIE_SITE_HOST`, `EGGIE_DOMAIN=<site domain>` and `EGGIE_API_BIND=127.0.0.1`
  to `/opt/eggie/.env` (written fresh on every run, as today);
- runs every compose command for the stack with
  `-f /opt/eggie/stack.yml -f /opt/eggie/runtime/stack.site.yml`;
- installs `/opt/eggie/tls/tls.yml`, the Traefik dynamic config naming
  `site.crt`/`site.key` as the default certificate, with the directory `2770 root:docker`
  so the API (docker group) can write the files and Traefik (root in its container) read them;
- records `"vm": "site"` in `connect.json` alongside the first login account, as it
  records `wsl`/`lima` today.

`snapshot-prep.sh` (new, `runtime/install/`) stops the stack and removes
`/opt/eggie/api.token`, `state.db`, `connect.json`, `site.env`, `tls/site.*`, then runs
`cloud-init clean --logs`. The runtime tree, images, `.env` and `runtime.env` stay.
On the next boot the installer regenerates the token (it only creates one when absent)
and the API starts with a fresh `state.db`.

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

`ApiConfig.site_host: str | None` from `EGGIE_SITE_HOST` (the console's hostname).
`domain` stays `EGGIE_DOMAIN`, which the installer sets to the site domain. Derived:

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

- `GET /api/health` and `GET /health` gain `"site": "<console hostname>" | null`.
- `GET /api/connect` (`infra/connect.py::facts`) returns `ssh: {host: <site host>,
  port: 22, user: <first login account>, key_file: null}` when `connect.json` says
  `"vm": "site"`; `"site"` joins the known kinds.
- `POST /projects/{id}/public` and the `/api` twin answer `409 not_available`
  ("every project on this server already has a public address") in site mode; `GET`
  reports `unavailable` with that reason, the same shape as "signed out" today.

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
| 443 | Traefik: console on the site host, `/api`, every project host under the site domain | Anyone on the internet; console and `/api` need a session from a service-minted handoff; projects have only their own auth |
| 80 | Redirect to 443 | Anyone |
| 22 | The login account | Holders of the SSH key the service installed |

The console and the projects live on different registered domains (decision 3), so a
project cannot set cookies the console's origin would read. The bearer API binds to
loopback. Project `ports:` are published by compose and blocked only by the service's
firewall (decision 14). The certificate key is `0600 root` on disk, written by the API
through the docker group, mounted read-only into Traefik; it covers only this
instance's names. `account.json` is deleted after import; tokens live in `state.db` as
today. The session cookie is `Secure`, `HttpOnly`, `SameSite=Strict`, path `/api`,
unchanged otherwise. A snapshot holds no token, database or key (section 4).

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
  survives a repair, one site variable without the other is refused, the compose
  commands carry the override only in site mode, `.env` carries the loopback bind and
  the site domain, `tls.yml` installed.
- new `test_snapshot_prep.py` (real bash): the listed files go, the runtime tree,
  `.env` and `runtime.env` stay.
- `test_stack_yml.py`: with `docker compose config` available, the merged site config
  publishes 80 and 443 and binds 39099 to loopback; skipped otherwise.
- console (Vitest): signed-out copy and public control depend on `health.site`.
- `docs/release-testing.md`: a manual "site" gate — build a snapshot, provision from
  it, open the console from the web app, start a project, reach it over `https` on
  `eggie.space`, reboot, confirm the certificate reload.

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
