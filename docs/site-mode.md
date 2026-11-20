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
| TLS | none | a list of certificates the service issues: one per server (`foo.eggie.io`, `foo.eggie.space`, `*.foo.eggie.space`) and one per custom domain |
| Custom domain | not shown | a project can also open on the owner's own domain (below) |
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
- creates `/opt/eggie/edge` (`2770 root:docker`: the API writes, Traefik reads), where the API
  puts the certificates and Traefik's dynamic file `edge.yml`;
- makes `account.json` `root:docker 0640` so the API can import and delete it;
- installs and enables `eggie-metadata-guard.service` (below);
- starts the api container once (a new token or a repair recreates it first, then the plain
  `up -d` leaves it alone), because the api rotates its account seed the moment it starts and
  a second start a moment later could lose the reply;
- records `"vm": "site"` in `connect.json`.

On start the API imports the account seed, rotates the token pair and fetches the certificates;
the server answers on 443 within a minute or two of boot.

## The edge

`stack.yml` is the same file in both modes; only the environment around it differs. Its router
rules admit `${EGGIE_SITE_HOST}` next to `localhost`, and its two port mappings read
`${EGGIE_API_BIND}` and `${EGGIE_EDGE_BIND}` (default `0.0.0.0`; a site sets both to loopback).

`stack.site.yml` is a compose override that replaces Traefik's `command` wholesale:

- entrypoint `web` moves to `:443` with TLS. Keeping the name `web` means no router label in the
  stack or in a project overlay changes;
- entrypoint `http` on `:80` redirects to it, at priority 100000 so no project router can answer
  plain HTTP; only the certificate check's path (below) is routed ahead of it;
- entrypoint `probe` on the edge port (`39080`), plain HTTP. The API's readiness probe
  (`infra/health.py`) dials `traefik:39080` over the `edge` network after a project starts;
  project routers name no entrypoint, so they answer there. The base mapping for that port is
  still published (compose merges `ports`), which is why `EGGIE_EDGE_BIND` pins it to loopback;
- the file provider watches `/opt/eggie/edge`, mounted read-only. The API generates `edge.yml`
  there: the TLS certificate list and the routers and redirects of custom domains.

Until a certificate lands, Traefik serves its own self-signed certificate.

### The certificates

`services/certificates.py` asks the service for its list of certificates on start when it holds
none, whenever a custom domain names one it lacks, and once a day. Each pair is stored under
`/opt/eggie/edge/certs/<id>/` (`cert.pem`, `key.pem` `0600`), staged and moved in with one rename.
A renewal is a new id: `edge.yml` then names both pairs until the old id leaves the service's
list, and a dropped id leaves disk on the next pass. Traefik reloads `edge.yml` as one file, so
it never sees a key without its certificate. A renewed certificate is picked up within a day.

## Custom domains

On a site, a project can also open on the owner's own domain, set up in the console's project
settings. The service decides everything about a domain: whether the name is allowed, whether DNS
(and a proxy or CAA record) lets a certificate be issued, the certificate and its renewal, the
status and the plain-language reason. The runtime mirrors the domain list, installs the
certificates the service lists and generates `edge.yml`.

- The address rule: a domain serves its project only while it is `Active` and its certificate is
  installed. While the primary domain serves, the project's Eggie address redirects to it
  (permanent, cached ten minutes) along with the other domains. When the domain is removed or
  has a problem, the Eggie address serves the project again.
- The project's first address (console, `eggie up`, push result) is the primary custom domain
  while it serves.
- `http://` on a custom host redirects to `https://`, except the CA's check at
  `/.well-known/acme-challenge/<token>`, which is answered on both ports without a redirect.
- A custom host reaches only the project's web service. The console, `/api` and other projects
  do not answer on it, and the challenge path is the only API route it can reach.
- Deleting a project forgets its domains on the service; replacing its files from a folder or
  pushing again keeps them. The Cloud deletes on the service only the domains of projects it
  deleted itself. A snapshot carries no domains or certificates.
- `eggie status` lists a project's domains; none of the commands changes one.

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
| 80 | Redirect to 443, and the CA's certificate check at `/.well-known/acme-challenge/<token>` | Anyone; the check is rate-limited (20 a minute per client address, 300 in all) and is the only unauthenticated API path besides the existing ones |
| 22 | The login account | Holders of the SSH key the service installed |

- The service's cloud firewall allows 22, 80 and 443 only. Project `ports:` published by compose
  stay published on the server and rely on that firewall.
- A custom host reaches only its project's web service, never the console, `/api` or another
  project; the only API path open on it is the certificate check.
- The bearer API and the plain-HTTP probe port bind to loopback.
- `eggie-metadata-guard.service` runs before dockerd and inserts a `DROP` for `169.254.169.254`
  into Docker's `DOCKER-USER` chain, so no bridged container can read the user data that seeded
  the account. A project with `network_mode: host` is outside that, as it is outside every
  container boundary.
- Certificate keys are `0600`, written by the API through the docker group, mounted read-only
  into Traefik; each covers only this server's names or one custom domain. There is never a zone-wide wildcard or a DNS
  token on a customer server.
- The session cookie is `Secure`, `HttpOnly`, `SameSite=Strict`, path `/api`.
