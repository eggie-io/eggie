# Site mode: the runtime on a server the Eggie service provisions

The same runtime that runs in a VM on the user's computer also runs on a server the Eggie
service creates for them (a DigitalOcean droplet). The user opens its console from the service's
web app and lands there signed in. This document describes how that works as built, and the
contract the server needs from the service. The dated design spec it grew from is
`superpowers/specs/2026-10-13-remote-instance-design.md`; this file is the one kept current.

Nothing in the host or the desktop app changes for site mode. Everything lives in `runtime/`.

## Build a snapshot

Run as root on a fresh Ubuntu 24.04 droplet, from `/`, with no site variables. The prep script
strips what is this machine's; power off and take the image.

Production — the newest `runtime-v*` tag; the boot update keeps every droplet made from it current:

```bash
cd / && curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/main/runtime/install/get.sh | bash \
  && bash /opt/eggie/runtime/install/snapshot-prep.sh \
  && poweroff
```

Development — pinned to the `dev` branch. `EGGIE_RUNTIME_URL` is persisted to `runtime.env`,
so the repair that cloud-init runs at provision time fetches the `dev` installer, not `main`'s:

```bash
cd / && curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/dev/runtime/install/get.sh \
  | EGGIE_RUNTIME_URL=https://raw.githubusercontent.com/eggie-io/eggie/dev/runtime/install/get.sh \
    EGGIE_RUNTIME_REF=dev bash \
  && bash /opt/eggie/runtime/install/snapshot-prep.sh \
  && poweroff
```

A branch ref runs the images pushed under its name (`build.sh --push --tag dev`) when ghcr has
both, the newest release's otherwise; `EGGIE_IMAGE_VERSION` overrides either. The boot update skips a branch-pinned install, so a
dev droplet stays on the commit baked in until `get.sh` is rerun with `EGGIE_RUNTIME_REF=dev`.

## Shape

| | Local VM | Site |
|---|---|---|
| Console | `http://localhost:39080` through the desktop's port forward | `https://foo.eggie.io` |
| Projects | `http://<id>.local.eggie.space:39080` | `https://<id>.foo.eggie.space` |
| A project's extra web service | `<service>--<id>.local.eggie.space` | `<service>--<id>.foo.eggie.space` |
| TLS | none | one certificate per server: `foo.eggie.io`, `foo.eggie.space`, `*.foo.eggie.space`, issued by the service |
| Sign-in | desktop handoff, or device code in the console | the server boots signed in; the browser gets a session from a code the web app mints |
| Bearer API (`:39099`) | all interfaces (WSL needs it) | loopback only |
| Public URLs (tunnel) | available | refused: every project already has a public address |

Why the two registered domains: a project is arbitrary code, and a subdomain can set cookies for
its parent. With the console on `eggie.io` and projects on `eggie.space`, a project can never
touch the console's origin. Why nested project hosts (`bar.foo.eggie.space`, not
`bar.eggie.space`): one wildcard DNS row and one certificate per server cover every project the
user will ever create; a flat name would need a DNS row per project and a key shared by every
server.

Why `--` between a service and its project: slugs may contain single hyphens, so `web-bar` could
be a project of its own. A slug never contains `--` (every run outside `[a-z0-9]` collapses to one
hyphen, for project ids and for service subdomains alike), so the two sides cannot be confused.

## How a server comes to be

1. **A snapshot** holds an installed runtime. It is built once per runtime release worth baking
   in: a plain Ubuntu 24.04 droplet, `get.sh` run from `/` with no site variables, then
   `runtime/install/snapshot-prep.sh`, which stops the stack and strips everything that makes the
   install this machine's: the API token, `state.db`, `connect.json`, `site.env`, `account.json`,
   the `tls/`, `tunnel/`, `uploads/`, `github/`, `projects/` and `agent-status/` trees, root's SSH
   key and shell history, and cloud-init's instance state and machine id. The runtime tree, the
   images, `.env` and `runtime.env` stay. The snapshot does not need rebuilding on every release:
   the boot update moves each server to the newest release on its first boot.
2. **The service provisions** a droplet from it (see the contract below): firewall, DNS rows,
   certificate, a device token pair for the owner's account, the login user's SSH key, and
   cloud-init user data that writes `/opt/eggie/account.json` and runs `get.sh` in repair mode with
   `EGGIE_SITE_HOST` and `EGGIE_SITE_DOMAIN`.
3. **`get.sh`** validates the two names (bare lowercase hostnames, both or neither) and persists
   them to `/opt/eggie/site.env`, so repairs, boot updates and manual reruns keep site mode.
4. **`install.sh`** sources `site.env` and, in site mode:
   - appends `EGGIE_SITE_HOST`, `EGGIE_DOMAIN=<site domain>`, `EGGIE_API_BIND=127.0.0.1` and
     `EGGIE_EDGE_BIND=127.0.0.1` to `/opt/eggie/.env`;
   - runs every stack compose command with `-f /opt/eggie/stack.yml -f /opt/eggie/runtime/stack.site.yml`;
   - creates `/opt/eggie/tls` (`2770 root:docker`: the API writes, Traefik reads) with
     `tls.yml`, Traefik's dynamic config naming `current/site.crt` and `current/site.key`;
   - makes `account.json` `root:docker 0640` so the API can import and delete it;
   - installs and enables `eggie-metadata-guard.service` (below);
   - starts the api container once (a new token or a repair recreates it first, then the plain
     `up -d` leaves it alone), because the api rotates its account seed the moment it starts and
     a second start a moment later could lose the reply;
   - records `"vm": "site"` in `connect.json`.
5. **The API starts**, imports the seed, rotates the token pair, fetches the certificate, and the
   server answers on 443 within a minute or two of boot.

## The edge

`stack.yml` is the same file in both modes; only the environment around it differs. Its router
rules admit `${EGGIE_SITE_HOST}` next to `localhost`, and its two port mappings read
`${EGGIE_API_BIND}` and `${EGGIE_EDGE_BIND}` (default `0.0.0.0`; a site sets both to loopback).

`stack.site.yml` is a compose override that replaces Traefik's `command` wholesale:

- entrypoint `web` moves to `:443` with TLS. Keeping the name `web` means no router label in the
  stack or in a project overlay changes;
- entrypoint `http` on `:80` redirects to it;
- entrypoint `probe` on the edge port (`39080`), plain HTTP. The API's readiness probe
  (`infra/health.py`) dials `traefik:39080` over the `edge` network after a project starts;
  project routers name no entrypoint, so they answer there. The base mapping for that port is
  still published (compose merges `ports`), which is why `EGGIE_EDGE_BIND` pins it to loopback;
- the file provider watches `/opt/eggie/tls`, mounted read-only.

Until the certificate lands, Traefik serves its own self-signed certificate.

### The certificate

`services/certificate.py` fetches `GET /v1/instances/me/certificate` on start when
`/opt/eggie/tls/current` does not resolve to a pair, then once a day from the sync loop. Each
pair lives in its own directory named by the certificate's expiry
(`/opt/eggie/tls/<not_after epoch>/site.crt` and `site.key`, key `0600`), and `current` is a
symlink to the newest. inotify on the watched directory reports only direct children, so files
written inside a new directory cause no reload; replacing the symlink is one event and Traefik
loads key and certificate together, never a mismatched pair. A newer `not_after` installs; an
older or equal one is ignored; old directories are removed at the start of the next check, never
right after a swap Traefik may still be reloading. The service renews at least 30 days before
expiry and the server picks the new one up within a day.

## Account and sign-in

**The seed.** `Account.seed_from` runs once at API start. If nothing is signed in and
`/opt/eggie/account.json` parses, it stores the device id and the token pair with the access
token already marked expired, deletes the file, and rotates the pair at once through the
existing refresh. The seeded tokens are never used for a request: cloud-init user data stays
readable on the droplet for its whole life, so the copy there must be dead as soon as it has
served its purpose. If the service is unreachable at that moment the first authenticated call
rotates instead; if the service refuses the pair the row is cleared.

**Managed account.** In site mode `Account` is *managed*: `POST /api/account/sign-in` and
`sign-out` (and their bearer twins) answer `403 site_account`. Without its pair a server could
neither redeem sign-in codes nor renew its certificate, and nothing on the server can re-seed it.
Detaching a server is revoking its device tokens on the service.

**Browser sign-in.** The web app's "open console" mints a single-use code bound to the owner and
to one server and redirects the browser to `https://foo.eggie.io/#handoff=<code>`. The console's
existing `#handoff` boot path posts it to `/api/session`. `SiteSignIn.redeem` first tries the
codes the API minted itself (desktop handoff, "open in browser"), which carry the `lh_` prefix and
are never forwarded; any other code goes outbound to `POST /v1/instances/handoff/redeem` with
the server's own token, at most ten a minute. A `200` creates a session like a local redemption;
anything else is the usual `handoff_invalid`, shown as a **Sign in** button.

**Direct visit.** A browser that opens `https://foo.eggie.io` with no session is sent to the web
app. `GET /api/health` carries `"web_app"` (the web app's address, from `EGGIE_WEB_URL`, default
`https://app.eggie.io`), and the console navigates to
`<web_app>/kitchens/open?host=foo.eggie.io`. That page asks for a web app login if there is none, calls
`POST /v1/instances/{id}/console` with the web session, and redirects back with
`#handoff=<code>`. The console only follows `web_app` when it is `https`, or `http` on
`localhost`.

## What the console shows

`GET /api/health` carries `"site": "<console host>" | null`, read from the same health answer
the boot sequence already fetches. Until it answers, a page on `localhost` assumes the usual VM
and any other address assumes nothing. On a site: a signed-out console goes
straight to the web app page above, with no screen to click through. After a code the server
refused it does not redirect again (that would loop) and shows a **Sign in** button instead,
with Try again beside it. The public URL control is absent; the agents screen shows the
cloud guide: the login and host come from the service's `GET /v1/instances` (falling back to what
install recorded), port 22, and the key as "the one you downloaded" since only the user has it; the account menu has no sign-out; and a server
whose account the service revoked gets a terminal "set it up again from the web app" screen,
with a link to the web app's kitchens list, instead of a sign-in button that cannot work.

## Pushing a project here

The local console's Push uploads a project to a kitchen; the kitchen side is `/api/transfers`,
mounted on a site only.

- `POST /api/transfers` (headers `Eggie-Transfer-Ticket`, `-Size`, `-Data: yes|no`,
  `-Replace: none|files|files-and-data`) redeems the ticket, resolves the project from the grant,
  checks overwrite rules and disk, and answers 201 `{push_id, token}`. An opened push with no upload
  in 10 minutes is dropped; a new open replaces it.
- `PUT /api/transfers/{push_id}/bundle` and `GET /api/transfers/{push_id}` take `Eggie-Transfer-Token`.
- Errors: `transfer_ticket_invalid`, `instance_busy` (429 past 10 opens a minute from one address
  or 60 in all; 409 while the project is busy), `already_on_instance`,
  `confirm_replace_data`, `instance_disk_full` (507), `bad_bundle`, `not_ready`, `cloud_unavailable`.
- `GET /api/ready` (open) answers 200 `{"ready": true}` when signed in and Docker answers, else 503
  `not_ready`. The service probes it before marking a kitchen running, so a release with it also
  serves `/api/transfers`.
- The redeem answer may carry `return_path`; `POST /api/session` returns it (validated) and the
  console lands on that page after sign-in.
- Pushed projects (`pushed_projects`) are skipped by the kitchen's registry sync: never registered
  or deleted there. On the sending side a locally deleted pushed project is forgotten, not deleted
  on the service.
- The service calls it uses: `POST /v1/transfers/tickets/redeem` and `.../complete` on the kitchen;
  `GET /v1/transfers/targets` and `POST /v1/transfers/tickets` on the sender.

## Security

Boundary rows for a site (the full table is in `security.md`):

| Way in | What it reaches | Who may use it |
|---|---|---|
| 443 | Traefik: console on the site host, `/api`, every project host under the site domain | Anyone; console and `/api` need a session from a service-minted code; projects have only their own auth |
| 80 | Redirect to 443 | Anyone |
| 22 | The login account | Holders of the SSH key the service installed |

- The service's cloud firewall allows 22, 80 and 443 only. Project `ports:` published by compose
  stay published on the droplet and rely on that firewall.
- The bearer API and the plain-HTTP probe port bind to loopback.
- `eggie-metadata-guard.service` runs before dockerd and inserts a `DROP` for `169.254.169.254`
  into Docker's `DOCKER-USER` chain, so no bridged container can read the user data that seeded
  the account. A project with `network_mode: host` is outside that, as it is outside every
  container boundary.
- The certificate key is `0600`, written by the API through the docker group, mounted read-only
  into Traefik, and covers only this server's names. There is never a zone-wide wildcard or a DNS
  token on a customer server.
- The session cookie is `Secure`, `HttpOnly`, `SameSite=Strict`, path `/api`.

## Contract with the Eggie service

Everything the server needs from the service. Endpoint paths are the ones the runtime calls;
renaming them is a change on both sides.

### Provisioning

1. Create the droplet from the snapshot with a cloud firewall allowing inbound 22, 80, 443 only.
2. Pick the instance name, e.g. `foo`. DNS: `foo.eggie.io` in Route53; `foo` and `*.foo` under
   `eggie.space` in Cloudflare, DNS-only (grey cloud).
3. Issue one Let's Encrypt certificate for `foo.eggie.io`, `foo.eggie.space` and
   `*.foo.eggie.space` (DNS-01 on both zones) and keep it; the server fetches it.
4. Mint a device for the owner's account (device id plus token pair, the same objects the
   device-code flow issues a desktop VM).
5. Pass this cloud-init user data. The login account must exist before the installer runs, since
   the runtime provisions every login account present at that moment:

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
    permissions: "0640"
    owner: root:docker
    content: |
      {"device_id": "…", "access_token": "…", "refresh_token": "…", "expires_in": 3600}
runcmd:
  - cd / && curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/main/runtime/install/get.sh
      | EGGIE_RUNTIME_REPAIR=1 EGGIE_SITE_HOST=foo.eggie.io EGGIE_SITE_DOMAIN=foo.eggie.space bash
```

`EGGIE_RUNTIME_REPAIR=1` keeps the release the snapshot holds and only configures. `cd /`
matters: per-account steps run as that account, and root's home is not enterable by others.
`expires_in` is ignored (the seeded access token is never used). Record the instance (name,
hostnames, device id, droplet id, owner) on the service; the web app's instance list and the
handoff check read from it.

### Authentication of the server's calls

Every call carries `Authorization: Bearer <device access token>`, refreshed through the existing
`POST /v1/auth/token/refresh`. Two requirements on that endpoint:

- **It rotates.** The refresh token it consumed and the access token issued with it are invalid
  afterwards, so the pair left in user data cannot be replayed.
- **It survives a lost reply.** If the server retries with the refresh token it just spent
  (because the answer never arrived), return the pair already issued, or accept the spent token
  for a short grace window. Otherwise the server's stored pair is dead, its next call gets
  `invalid_grant`, it clears its account, and since sign-in is refused on a site, the server is
  locked out until re-provisioned.

### `GET /v1/instances`

Called with the device token for the console's instance switcher (through `GET /api/instances`).
Returns the account's instances. The runtime reads `id`, `display_name`, `console_hostname`,
`status`, `signed_out` and `is_owner`, and computes `current` itself from its own site host. It lists only; opening a kitchen's console still needs
`POST /v1/instances/{id}/console` with a web session.

The desktop window opens kitchens in the system browser: it runs pywebview in private mode, so it
would never share the browser's web app sign-in and would ask for a login on every launch.

### `GET /v1/instances/me/certificate`

```json
200: {"names": ["foo.eggie.io", "foo.eggie.space", "*.foo.eggie.space"],
      "certificate": "-----BEGIN CERTIFICATE-----\n…(full chain)…",
      "private_key": "-----BEGIN PRIVATE KEY-----\n…",
      "not_after": "2027-01-11T10:00:00Z"}
404: {"error": {"code": "no_certificate", "message": "…"}}
```

Called on start when no certificate is installed, then once a day. The server replaces its files
only when `not_after` is later than the one it holds; a 404 or any failure leaves them in place.

### `POST /v1/instances/handoff/redeem`

```json
request: {"code": "k3x9m2p7qa…"}
200:     {"ok": true}
401/403/404/409: {"error": {"code": "handoff_invalid", "message": "…"}}
```

`200` means "this code was minted for this server's owner and has not been used". Codes must be
single-use, short-lived (a minute is plenty), bound to one server, and consumed on redemption
even if the server then fails to set a cookie. Codes the service mints never start with `lh_`.

### Lifecycle

- Renew the certificate at least 30 days before `not_after`.
- Deleting an instance: revoke its device tokens; the server is disposable.
- The web app serves `/kitchens/open?host=<console host>`. It checks that
  the logged-in owner has an instance with that console hostname, mints the console code with
  `POST /v1/instances/{id}/console`, and redirects to `https://<host>/#handoff=<code>`. Its own address is the server's
  `EGGIE_WEB_URL`.
- The web app shows each instance's console hostname and an "open console" action. Projects on
  an instance appear through the existing project registry sync under the instance's device id.

## Open points

- A one-time bootstrap code in user data, exchanged outbound for the token pair, would keep
  tokens out of user data entirely. First-boot rotation plus the metadata guard cover the gap for
  now; the exchange is a contract change for a later round.
- Until the refresh grace window exists on the service, a stranded server is re-provisioned.
- User-attached custom domains, Cloudflare proxying and forward-auth in front of projects are out
  of scope for this version.
- The live gates for a site are `release-testing.md` S1–S12 and S13–S18 (push); the branch that built site mode had
  not run on a real droplet when this was written.
