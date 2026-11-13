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
| Site, port `22` | The login account | Whoever holds the login account's SSH key (site mode only) |
| Site, `/api/transfers` | A pushed project's bundle, into the project store | Whoever holds a one-use push ticket (site mode only) |

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
  ACLs on `/opt/eggie/projects` and read access to `api.token`. `sudo` needs no password. On WSL2
  `wsl -d eggie-vm` opens as the non-root `eggie` account, but `eggie` is in the `docker` group and
  has passwordless `sudo`, and `wsl -d eggie-vm -u root` from Windows needs nothing at all. Whoever
  holds an SSH key into the VM holds the VM.
- **Coding agents.** They run as a login account, so they have everything above.

## The user's own computer

These settings keep agents from damaging the user's files by accident. They are not a security
boundary.

- **Lima:** `host/providers/eggie.yaml` has `mounts: []`, so no macOS folder is shared.
- **WSL2:** WSL mounts every Windows drive at `/mnt/<drive>` and puts Windows folders on `PATH`
  by default. `install.sh` turns both off on every install, update and repair
  (`lib/wsl-conf.py`). The settings go in `/etc/wsl.conf`:

  ```ini
  [automount]
  enabled=false

  [interop]
  appendWindowsPath=false
  ```

  Interop (running Windows `.exe` files) stays on: it is one kernel registration every WSL distro
  shares, so turning it off in `eggie-vm` breaks it in the user's other distros.

  WSL reads the file only when the distro starts. Setup restarts a VM it has just created right after the first install, before anyone uses it. A VM
  that was already running when it got the setting keeps the old behaviour until its next start,
  for example a Windows restart or `wsl --shutdown`. Projects come in by HTTP upload (Import), not
  through a shared folder.

What this does not stop:

- Any process in the VM can run a Windows `.exe` it has there; it runs on Windows as the user.
- Root in the VM (`eggie` has passwordless `sudo` and the `docker` group) can mount a drive by
  hand or edit `wsl.conf`.
- Every WSL2 distro on the computer runs in one shared utility VM with one kernel. A kernel
  escape from any distro reaches all of them.
- Windows can always reach into the VM: `wsl -d eggie-vm -u root` needs no password, and
  `\\wsl.localhost\eggie-vm` shows its files to any Windows program.

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

## Recipe actions

Commands come only from the Eggie service and run as the login account, never as root.

- **The terminal WebSocket checks for itself.** The auth middleware never sees a WebSocket, so the
  route checks `Host`, a required `Origin` and the session cookie before accepting. It exists only
  under `/api`; the bearer-token mount has no terminal.
- **The helper socket is `root:docker 0660`.** Whoever can use the Docker socket can ask `eggie-actions`
  to run or attach to an action; that is the same trust as the Docker socket. The agent's account is
  in `docker`, so it can see an action's terminal, including what the person types into it.
- **A one-off action keeps no log**, so what it prints, a typed code included, stays out of files. Long-running
  actions log their output to `/opt/eggie/actions/logs/`.

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

The idle-session reaper (`vm.md`) runs as root and can stop any user's process, so it stops only
a Claude Code Desktop process it can tie to a session transcript: no `ccd-cli` binary, no session
id or no transcript, and the process is left alone and logged. Ending sessions with logind's
`KillUserProcesses=yes` or `StopIdleSessionSec` is ruled out: both end Desktop's SSH session when
the user disconnects, which kills the task they meant to leave running.

## Site mode

On a server the Eggie service provisions, the runtime exposes 443, 80 and 22 behind a firewall
the service manages; everything above about the inside of the boundary still holds. The console
and `/api` need a session the service issues, and projects are public with whatever auth they
have themselves.

## Before adding a way in

If a change lets anything outside the user's machine reach the VM (a new forward, a non-loopback
bind, a cloud-to-VM channel, a new tunnel route), it crosses this boundary. Give it its own
authentication, don't reuse the API token, and update this page.
