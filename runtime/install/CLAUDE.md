# runtime/install/ — provisioning scripts

Run as root inside the VM (WSL, Lima, or a cloud VM). Tests: `tests/runtime/test_get_sh.py`,
`test_install_shell.py`, `test_install_agents.py`, `test_login_users.py`, `test_github_apply.py`.

## `get.sh` is a live contract for every shipped host

Hosts fetch it from **`main`**, not from a tag, so an edit here reaches every installed host at once.
Keep backward compatible: its env vars (`OMELET_RUNTIME_REPO`, `OMELET_RUNTIME_REF`,
`OMELET_RUNTIME_REPAIR`), the marker path `/opt/omelet/runtime.version`, and its exit semantics.

Flow: `resolve_ref` (explicit `OMELET_RUNTIME_REF` → installed ref on repair → highest `runtime-v*`
tag by `sort -V`) → download that ref's tarball → replace `/opt/omelet/runtime/` with its `runtime/`
→ run `install/install.sh <ref> [--repair]`.

## `install.sh` — numbered steps, order matters

Docker (from Docker's repo, guarded on the package) → `edge` network → `/opt/omelet` permissions →
docker GID into `.env` for `stack.yml`'s `group_add` → token (only if absent) → the compose stack
(always pulls; recreates the api on a new token or repair) → Node ≥ 22.20 from NodeSource →
`/usr/local/bin/omelet` → `/etc/claude-code/CLAUDE.md` → per account (root + `lib/login-users.sh`):
the Codex block and `~/projects` link (`lib/install-agents.sh`) and
`npx -y skills@1.5.26 add $SKILLS_SOURCE -s '*' -g -a claude-code codex -y </dev/null`
(`SKILLS_SOURCE` defaults to `ihorklymchukdev/omelet-skills`, unpinned on purpose;
`OMELET_SKILLS_SOURCE` overrides) → the VM kind (`wsl`/`lima`/`other`) and first login user into
`/opt/omelet/connect.json` (read by `GET /connect`) → **`runtime.version` last**, so a failed
install never looks installed. Re-running is safe; the comments in the script explain each guard —
read them before reordering.

`lib/github-apply.sh` runs as root from the `systemd/omelet-github.path` unit when the API writes
`/opt/omelet/github/desired.json` (see `runtime/omelet_api/CLAUDE.md`).

## Testing

`bash -n` plus text assertions over `install.sh`; `resolve_ref` sourced from `get.sh` with a fake
`git` on `PATH`; `install-agents.sh`/`login-users.sh` run against temporary homes. Nothing touches
the network — apt, NodeSource, npm and ghcr are covered only by the live-VM acceptance run.

## Things that will bite you

- Step 4 runs `chmod -R g+rwX /opt/omelet` on every install, which widens
  `/opt/omelet/github/token`. The modes are reasserted right after the sweep; keep that order. Same
  for the API token: it must be written after the sweep.
- Login accounts are never the API's uid 1000: WSL2 has only root, and Lima's user carries the macOS
  uid. The API writes into projects through the docker group, so anything it creates there needs
  `umask 002` (see `clone_argv`), and `/etc/gitconfig` trusts `safe.directory '*'`.
- Use `/usr/bin/docker` by absolute path: Docker Desktop's WSL integration can put its own `docker`
  on `PATH`, which talks to Desktop's engine instead of this VM's.
