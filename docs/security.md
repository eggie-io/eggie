# Trust boundary

**The VM is the boundary.** Everything inside it is trusted the same as root in the VM, and
nothing inside it is protected from anything else inside it. That is the right trade for a
single-user VM on the user's own machine. It stops being right the moment something outside the
user's machine can reach in, so read this before adding any way in.

## Who can reach the VM from outside

| Path | Reaches | Bound to |
|---|---|---|
| API, port `39099` | The API at `/` (bearer token) | Host loopback only |
| Edge, port `39080` | Traefik: the console, the API at `/api`, project URLs | Host loopback only |
| SSH, port `39022` (Lima) | A login account in the VM | Host loopback only |
| Public URL (optional) | One project, through the Eggie service's Cloudflare tunnel | Outbound from the `tunnel` container |
| Site, port `443` | Traefik: the console on the site host, the API at `/api`, every project under the site domain | The internet (site mode only) |
| Site, port `80` | A redirect to 443 | The internet (site mode only) |
| Site, port `22` | The login account | Whoever holds the SSH key the service installed (site mode only) |

The host-side forwards are loopback-only: WSL2's `localhostForwarding` puts guest ports onto the
Windows host's `127.0.0.1`, and Lima's `portForwards` (`host/providers/eggie.yaml`) default to
`127.0.0.1`. Nothing on the user's network can connect to any of them.

The API listens on `0.0.0.0` inside the VM, not `127.0.0.1`, because WSL2's `localhostForwarding`
only surfaces guest sockets bound to all interfaces. The cost is that every container in the VM can
reach it too; that is what the token is for (below).

## What is inside the boundary

Each of these is root-equivalent in the VM:

- **The Docker socket.** The `api` service mounts `/var/run/docker.sock` read-write, and Traefik
  mounts it read-only. Access to the socket is root in the VM.
- **Every project container.** Projects are arbitrary compose files. Nothing stops one from
  mounting the Docker socket, running `privileged`, or using the host network, and the API does
  not inspect or refuse any of these.
- **Every login account.** `install.sh` puts each login account in the `docker` group and gives it
  ACLs on `/opt/eggie/projects` and read access to `api.token`. `sudo` needs no password (on WSL2
  the account is `eggie`, and `wsl -d ... -u root` needs nothing at all). Whoever holds an SSH key into the VM holds the VM.
- **Coding agents.** They run as a login account, so they have everything above.

## What each secret protects, and what it doesn't

| Secret | Where | Protects against | Does not protect against |
|---|---|---|---|
| API token | `/opt/eggie/api.token` (0640 root:docker, plus an ACL per login account) | A project container calling the API's `/` routes by accident | Anything with the Docker socket, any login account, any process that reads the file. It is one shared, VM-wide value that never rotates |
| Console session | `eggie_session` cookie, `/api` routes only | Other websites the user visits: the cookie is `SameSite=Strict`, and `/api` checks `Host` and (on writes) `Origin` against `localhost`/`127.0.0.1` on the edge port, which also blocks DNS rebinding | Anything inside the VM: a session comes from a handoff code, and `POST /sessions/handoff` only needs the API token |
| GitHub token | `/opt/eggie/github/token` (0600) | Login accounts reading it directly | Root, the Docker socket, the API |
| Cloud account tokens | `/opt/eggie/state.db` | Nothing beyond `/opt/eggie`'s group permissions | Anything in the `docker` group |
| Tunnel token | `/opt/eggie/tunnel/token` (0640 root:docker; present only while a public URL is on) | Login accounts outside the `docker` group | Anything in the `docker` group |
| Project secrets | `/opt/eggie/state.db`, outside project folders; never synced | Leaking into project files, git and chat | Anything root-equivalent in the VM; anyone who can `docker inspect` or `docker exec ... env`, coding agents included. They are not protected from the agent |

**The API token is not authentication.** It tells the host apart from a stray container and
nothing more. Never treat holding it as proof of who is calling, never send it off the machine, and
never put a route behind it alone that would be unsafe if every process in the VM could call it.
A service-issued per-device token is meant to replace it in a later phase; until then, a new way to
reach the API from outside the user's machine needs its own authentication.

## Public URLs

A public URL is the one path in from outside the user's machine. The Eggie service runs the
Cloudflare tunnel; the VM runs only the `cloudflared` client, which connects out. The service
rewrites `Host` to the project's local hostname, so Traefik routes the request to that project
alone. The console and the API at `/api` only match `Host: localhost` or `127.0.0.1`, so a public
visitor can't reach them through Traefik.

Two things hold that up, and neither is enforced inside the VM:

- **The service must never forward a `Host` of `localhost` or `127.0.0.1`.** If it did, a public
  visitor would get the console's routes. Without a session they'd stop at the login check, but
  that check is all that would stand in the way.
- **The `tunnel` container can reach every port the VM publishes on `0.0.0.0`**, through the
  network's gateway. That includes the API on `39099` and any project's `ports:`. Only the API
  token stands between the `cloudflared` process and the API. The `tunnel` container sits on the
  `tunnel` network with Traefik, not on `edge`, but that does not keep it off published ports.

A project shared by public URL is exposed to the internet with whatever auth it has itself.

## Site mode (a server the service provisions)

On a site the boundary is the server's firewall, which the service manages: 22, 80 and 443
only. Inside it, everything above still holds — every login account, project and container is
root-equivalent — plus:

- The console and `/api` are on the internet. A session comes only from a handoff code the
  service minted for this instance's owner and the instance redeemed with the service
  (`services/site.py`); the cookie is `Secure`. The console and the projects sit on different
  registered domains (`eggie.io` / `eggie.space`), so a project cannot set a cookie the console's
  origin would read. The instance redeems at most ten codes a minute with the service; the
  service owns single use and expiry.
- Projects are public with whatever auth they have themselves, like public URLs locally.
- The bearer API binds to loopback (`EGGIE_API_BIND`). Project `ports:` are still published on
  all interfaces; only the firewall blocks them.
- The certificate key is `0600`, owned by the API's user, written by the API, mounted read-only into Traefik; it covers
  this instance's three names only. The seeded token file is deleted on import.
- The seeded token pair is rotated at first boot, and sign-out is refused on a site.
- The edge port (39080) is the plain-HTTP probe entrypoint and serves every project host; on a site
  it is published on loopback only (`EGGIE_EDGE_BIND`).
- The desktop window also shows the Eggie web app and kitchen consoles (pages from the internet) when
  the person switches or signs in. The bridge guard refuses them as it refuses the VM console, and its
  documented residual race applies to them too. Minting a kitchen code still needs a person's web app
  session; the device token only lists kitchens (`GET /v1/instances`).
- A snapshot made with `snapshot-prep.sh` holds no token, database or key.
- Containers cannot reach the cloud metadata service (`169.254.169.254`): it serves the user
  data that seeded the account token, and a project's code would otherwise read it. A droplet-only
  `DROP` rule in `DOCKER-USER`, installed by `eggie-metadata-guard.service` before Docker starts. It covers bridged
  containers only; a project with `network_mode: host` sits on the host's network and is outside it, as it is outside every container boundary already.

## Before adding a way in

If a change lets anything outside the user's machine reach the VM (a new forward, a non-loopback
bind, a cloud-to-VM channel, a new tunnel route), it crosses this boundary. Give it its own
authentication, don't reuse the API token, and update this page.
