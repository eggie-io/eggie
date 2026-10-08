# runtime/install/ — provisioning scripts

Run as root inside the VM (WSL, Lima, or a cloud VM). Tests: `tests/runtime/test_get_sh.py`,
`test_install_shell.py`, `test_install_agents.py`, `test_agents_manifests.py`, `test_login_users.py`, `test_github_apply.py`, `test_agents_run.py`,
`tests/runtime/test_boot_update.py`, `test_image_version.py`.

## `get.sh` is a live contract for every shipped host

Hosts fetch it from **`main`**, not from a tag, so an edit here reaches every installed host at once.
Keep backward compatible: its env vars (`EGGIE_RUNTIME_REPO`, `EGGIE_RUNTIME_REF`,
`EGGIE_RUNTIME_REPAIR`, `EGGIE_RUNTIME_API`, `EGGIE_RUNTIME_UPDATE`), the marker path
`/opt/eggie/runtime.version`, and its exit semantics.

Flow: `resolve_ref` (explicit `EGGIE_RUNTIME_REF` → installed ref on repair → highest `runtime-v*`
tag by `sort -V`, filtered to `EGGIE_RUNTIME_API` when set — the newest tag whose
`runtime/release.json` declares an accepted `api`) → download that ref's tarball → replace
`/opt/eggie/runtime/` with its `runtime/` → run `install/install.sh <ref> [--repair]`. Every
install also writes `/opt/eggie/runtime.env` (`EGGIE_RUNTIME_URL`, `EGGIE_RUNTIME_REPO`), the
source a later update reads. The whole flow — install, repair and update alike — runs under
`flock /opt/eggie/update.lock`, so a host-started update and the boot unit can't interleave.

### Update mode (`EGGIE_RUNTIME_UPDATE=1`)

A no-op when the resolved ref is already installed, or sorts below an installed `runtime-v*` tag
(a failed `release.json` fetch must never cause a downgrade). Otherwise: stage the new tree and
`docker compose ... pull` its images before touching anything installed (with `EGGIE_VERSION`
set to the new release's, since `.env` still names the installed one); on success, move
`/opt/eggie/runtime` to `/opt/eggie/runtime.prev` and swap the new tree in; if `install.sh`
then fails, restore `runtime.prev` and re-run its `install.sh` to roll back, exiting with the new
`install.sh`'s code. The swap records the previous ref in
`runtime.prev.version`; a successful update deletes both. Every run starts by repairing what an
interrupted one left: `runtime.prev` with no marker (killed while the new `install.sh` ran) is
moved back and its `install.sh` re-run with the recorded ref; `runtime.prev` without `runtime` (a
crash between the two moves) is moved back. `boot-update.sh` reads `runtime.prev.version` when
the marker is missing, so the next boot does this and then updates again.

### `lib/boot-update.sh` + `systemd/eggie-update.service`

The unit `install.sh` installs and **enables but never starts** — it only ever runs at the
VM's own boot. It is `Type=exec`, not oneshot, so boot never waits for the update, and it has no
`Wants=network-online.target` (that pulls in `systemd-networkd-wait-online`, which stalls under WSL). It skips a VM installed from a ref that isn't a `runtime-vN.N.N` tag (a pinned
branch), reads the accepted API from `/opt/eggie/host.json` (falling back to the installed
release's own `api` when the host never wrote one), retries fetching `get.sh` a few times in case
the network comes up after the unit starts, then runs it with `EGGIE_RUNTIME_UPDATE=1`.

## `install.sh` — numbered steps, order matters

Docker (from Docker's repo, guarded on the package) → `edge` network → `/opt/eggie` permissions →
docker GID and the image version (`lib/image-version.sh`: a `runtime-vX.Y.Z` ref runs `X.Y.Z`,
any other ref the newest release's images or `EGGIE_IMAGE_VERSION`) into `.env` for `stack.yml` → token (only if absent) → the compose stack
(always pulls; recreates the api on a new token or repair) → git, gh, bubblewrap (Codex's command sandbox) → Node ≥ 22.20 from NodeSource →
`/usr/local/bin/eggie` (built from `cli/eggie_cli/` with `zipapp`) → the system-wide instruction files the agent manifests name (`lib/agents.py instructions --system`) → `/etc/profile.d/eggie-cwd.sh` (interactive login shells in `$HOME` open in `~/projects`) → per account (root + `lib/login-users.sh`):
each manifest's per-account instruction block and the `~/projects` link (`lib/install-agents.sh`) and
`npx -y skills@1.5.26 add $SKILLS_SOURCE -s '*' -g -a $(agents.py skills) -y </dev/null`
(`SKILLS_SOURCE` defaults to `eggie-io/eggie-skills`, unpinned on purpose;
`EGGIE_SKILLS_SOURCE` overrides) → the VM kind (`wsl`/`lima`/`other`) and first login user into
`/opt/eggie/connect.json` (read by `GET /connect`) → **`runtime.version` last**, so a failed
install never looks installed. Re-running is safe; the comments in the script explain each guard —
read them before reordering.

`lib/github-apply.sh` runs as root from the `systemd/eggie-github.path` unit when the API writes
`/opt/eggie/github/desired.json` (see `runtime/eggie_api/CLAUDE.md`).

`lib/agents-run.sh` runs as root from `systemd/eggie-agents.path` whenever the API writes
`/opt/eggie/agent-status/check`: `lib/agents.py run` marks each agent in the manifests
(`runtime/agents/`) connected or not and runs `setup.run` for every `setup/<id>` request number
above the one it last handled, into `status.json`. systemd drops triggers that arrive during a pass,
so the runner re-reads `check` and passes again (at most 5); a 10-minute setup holds detection up
meanwhile.

## Testing

`bash -n` plus text assertions over `install.sh`; `resolve_ref` sourced from `get.sh` with a fake
`git` on `PATH`; `install-agents.sh`/`login-users.sh` run against temporary homes. Nothing touches
the network — apt, NodeSource, npm and ghcr are covered only by the live-VM acceptance run.

## Things that will bite you

- Step 4 runs `chmod -R g+rwX /opt/eggie` on every install, which widens
  `/opt/eggie/github/token`. The modes are reasserted right after the sweep; keep that order. Same
  for the API token: it must be written after the sweep.
- Login accounts are never the API's uid 1000: WSL2 has only root, and Lima's user carries the macOS
  uid. The API writes into projects through the docker group; `/etc/gitconfig` trusts
  `safe.directory '*'`. Group membership alone is not enough for the accounts: Lima multiplexes
  every session over the ssh control master opened at boot, before step 10's `usermod`, so those
  sessions never get the docker group. Step 11 therefore also grants each account access by uid
  (ACLs on `/opt/eggie/projects` and `api.token`), and step 4's default ACL makes new project
  entries group-writable whatever the creator's umask (the API's is 022).
- Every console status poll starts `eggie-agents.service`, so journald shows a Started/Finished
  pair every 3 s while an agents screen is open; and `systemctl start eggie-agents.service` in
  `install.sh` step 11c blocks while a requested setup runs.
- Use `/usr/bin/docker` by absolute path: Docker Desktop's WSL integration can put its own `docker`
  on `PATH`, which talks to Desktop's engine instead of this VM's.
