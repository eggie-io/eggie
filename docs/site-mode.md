# Site mode: the runtime on a server the Eggie service provisions

The same runtime that runs in a VM on the user's computer also runs on a server the Eggie
service creates for them. The user opens its console from the service's web app and lands there
signed in. This document describes what the runtime does in that mode.

The service side of site mode is documented privately.

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

## Turning site mode on

`get.sh` takes two names, `EGGIE_SITE_HOST` (the console host) and `EGGIE_SITE_DOMAIN` (the
domain projects live under). It validates them (bare lowercase hostnames, both or neither) and
persists them to `/opt/eggie/site.env`, so repairs, boot updates and manual reruns keep site mode.

`install.sh` sources `site.env` and, in site mode:

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

On start the API imports the account seed, rotates the token pair and fetches the certificate;
the server answers on 443 within a minute or two of boot.

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

`services/certificate.py` fetches the certificate from the service on start when
`/opt/eggie/tls/current` does not resolve to a pair, then once a day from the sync loop. Each
pair lives in its own directory named by the certificate's expiry
(`/opt/eggie/tls/<not_after epoch>/site.crt` and `site.key`, key `0600`), and `current` is a
symlink to the newest. inotify on the watched directory reports only direct children, so files
written inside a new directory cause no reload; replacing the symlink is one event and Traefik
loads key and certificate together, never a mismatched pair. A newer `not_after` installs; an
older or equal one is ignored; old directories are removed at the start of the next check, never
right after a swap Traefik may still be reloading. A renewed certificate is picked up within a day.

## Account and sign-in

**The seed.** `Account.seed_from` runs once at API start. If nothing is signed in and
`/opt/eggie/account.json` parses, it stores the device id and the token pair with the access
token already marked expired, deletes the file, and rotates the pair at once through the
existing refresh. The seeded tokens are never used for a request: cloud-init user data stays
readable on the server for its whole life, so the copy there must be dead as soon as it has
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
are never forwarded; any other code goes outbound to the service with the server's own token. A
code the service accepts creates a session like a local redemption; anything else is the usual
`handoff_invalid`, shown as a **Sign in** button.

**Direct visit.** A browser that opens `https://foo.eggie.io` with no session is sent to the web
app. `GET /api/health` carries `"web_app"` (the web app's address, from `EGGIE_WEB_URL`, default
`https://app.eggie.io`), and the console navigates to
`<web_app>/kitchens/open?host=foo.eggie.io`. That page asks for a web app login if there is none,
gets a code for this server, and redirects back with `#handoff=<code>`. The console only follows
`web_app` when it is `https`, or `http` on `localhost`.

## What the console shows

`GET /api/health` carries `"site": "<console host>" | null`, read from the same health answer
the boot sequence already fetches. Until it answers, a page on `localhost` assumes the usual VM
and any other address assumes nothing. On a site: a signed-out console goes
straight to the web app page above, with no screen to click through. After a code the server
refused it does not redirect again (that would loop) and shows a **Sign in** button instead,
with Try again beside it. The public URL control is absent; the agents screen shows the
cloud guide: the login and host come from the service's instance list (falling back to what
install recorded), port 22, and the key as "the one you downloaded" since only the user has it; the account menu has no sign-out; and a server
whose account the service revoked gets a terminal "set it up again from the web app" screen,
with a link to the web app's kitchens list, instead of a sign-in button that cannot work.

## Pushing a project here

The local console's Push uploads a project to a kitchen; the kitchen side is `/api/transfers`,
mounted on a site only.

- A sign-in code can carry a page to land on (a push's "add secrets" link uses it);
  `POST /api/session` returns it (validated) and the console lands on that page after sign-in.
- Pushed projects (`pushed_projects`) are skipped by the kitchen's registry sync: never registered
  or deleted there. On the sending side a locally deleted pushed project is forgotten, not deleted
  on the service.

## Security

Boundary rows for a site (the full table is in `security.md`):

| Way in | What it reaches | Who may use it |
|---|---|---|
| 443 | Traefik: console on the site host, `/api`, every project host under the site domain | Anyone; console and `/api` need a session from a service-minted code; projects have only their own auth |
| 80 | Redirect to 443 | Anyone |
| 22 | The login account | Holders of the SSH key the service installed |

- The service's cloud firewall allows 22, 80 and 443 only. Project `ports:` published by compose
  stay published on the server and rely on that firewall.
- The bearer API and the plain-HTTP probe port bind to loopback.
- `eggie-metadata-guard.service` runs before dockerd and inserts a `DROP` for `169.254.169.254`
  into Docker's `DOCKER-USER` chain, so no bridged container can read the user data that seeded
  the account. A project with `network_mode: host` is outside that, as it is outside every
  container boundary.
- The certificate key is `0600`, written by the API through the docker group, mounted read-only
  into Traefik, and covers only this server's names. There is never a zone-wide wildcard or a DNS
  token on a customer server.
- The session cookie is `Secure`, `HttpOnly`, `SameSite=Strict`, path `/api`.
