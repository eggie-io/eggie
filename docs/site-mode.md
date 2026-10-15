# Site mode: the runtime on a server the Eggie service provisions

The same runtime that runs in a VM on the user's computer also runs on a server the Eggie
service creates for them (a DigitalOcean droplet). The user opens its console from the service's
web app and lands there signed in. This document describes how that works as built, and the
contract the server needs from the service. The dated design spec it grew from is
`superpowers/specs/2026-10-13-remote-instance-design.md`; this file is the one kept current.

Nothing in the host or the desktop app changes for site mode. Everything lives in `runtime/`.

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

## Building the snapshot by hand

What the service developer (or you) runs once per runtime release worth baking in.

1. **Droplet.** Ubuntu 24.04, 2 GB RAM or more (the installer adds a swapfile, but npm and the
   image pulls want headroom), your SSH key. Any region; amd64 or arm64 both work. SSH in as root.
2. **Install, with no site variables.** The names come later, from cloud-init on each droplet.

   ```bash
   cd / && curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/main/runtime/install/get.sh | bash
   ```

   `cd /` is required: the installer runs per-account steps as that account, and root's home is
   not enterable by other users (npm fails with `EACCES` otherwise). The run takes a few minutes
   (Docker, the two images, Node, the skills) and ends with `Eggie runtime runtime-vX.Y.Z installed`.
   `EGGIE_RUNTIME_REF=runtime-vX.Y.Z` pins a release instead of the newest.
3. **Check it before scrubbing.** `docker ps` shows `traefik`, `api` and `web`;
   `curl -s http://127.0.0.1:39080/ | head -c 200` returns the console's HTML.
4. **Scrub.** This stops the stack and removes everything that makes the install this machine's
   (token, database, projects, GitHub token, cloud-init state, root's key and history):

   ```bash
   bash /opt/eggie/runtime/install/snapshot-prep.sh
   ```

   It ends with `ready to snapshot: power off and take the image`. Nothing on the droplet is
   usable after this; do not reboot it.
5. **Image.** Power the droplet off from the DigitalOcean panel (not `shutdown`, which can race
   the snapshot) and take the snapshot. Delete the droplet.

### Before the site-mode release is tagged

Site mode ships on `feature/73-remote-instance` until PR #75 merges and a `runtime-v*` tag with
it exists. Until then a snapshot has to come from the branch, and a branch ref runs the newest
*release's* images, which do not contain the site-mode API and console. So:

1. Build and push throwaway images from the branch on a dev machine (arm64 needs the qemu binfmt
   handler registered first on WSL2: `docker run --privileged --rm tonistiigi/binfmt --install arm64`):

   ```bash
   packaging/images/build.sh --push --tag dev
   ```

2. Install from the branch, naming those images and the branch's own `get.sh` (so repairs and the
   provisioning `runcmd` fetch one that knows the site variables):

   ```bash
   BR=feature/73-remote-instance
   cd / && curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/$BR/runtime/install/get.sh \
     | EGGIE_RUNTIME_REF=$BR EGGIE_IMAGE_VERSION=dev \
       EGGIE_RUNTIME_URL=https://raw.githubusercontent.com/eggie-io/eggie/$BR/runtime/install/get.sh bash
   ```

   A commit SHA works in place of the branch name. The boot updater skips a machine installed
   from anything but a `runtime-v*` tag, so such a snapshot stays on the branch until re-imaged.
3. The provisioning user data must then fetch `get.sh` from the branch URL too (the `runcmd` in
   the contract below names `main`).

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
anything else is the usual `handoff_invalid`, shown as "open the console from the web app again".

## What the console shows

`GET /api/health` carries `"site": "<console host>" | null`, read from the same health answer
the boot sequence already fetches. Until it answers, a page on `localhost` assumes the usual VM
and any other address assumes nothing. On a site: the signed-out screen says to reopen the
console from the Eggie web app; the public URL control is absent; the agents card shows the
server's SSH host and port 22 with no key file; the account menu has no sign-out; and a server
whose account the service revoked gets a terminal "set it up again from the web app" screen
instead of a sign-in button that cannot work.

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
- The web app shows each instance's console hostname and an "open console" action. Projects on
  an instance appear through the existing project registry sync under the instance's device id.

## Open points

- A one-time bootstrap code in user data, exchanged outbound for the token pair, would keep
  tokens out of user data entirely. First-boot rotation plus the metadata guard cover the gap for
  now; the exchange is a contract change for a later round.
- Until the refresh grace window exists on the service, a stranded server is re-provisioned.
- The instance switcher (console and desktop), user-attached custom domains, Cloudflare proxying
  and forward-auth in front of projects are out of scope for this version.
- The live gates for a site are `release-testing.md` S1–S12; the branch that built site mode had
  not run on a real droplet when this was written.
