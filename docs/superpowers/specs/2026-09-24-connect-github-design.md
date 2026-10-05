# Connect GitHub — design

## 1. Goal

A non-technical user connects their GitHub account from the Eggie console with one
button and never opens a terminal. Afterwards every VM login account where coding agents
run is authenticated as that user for `gh` and for `git` over https. The user can pick one
of their repositories, most recently updated first, and have it cloned as an Eggie
project. The coding agent never runs a GitHub login itself.

Done when, on a fresh VM:

- Connect GitHub → pick a private repo → clone works end to end.
- Inside the VM, as the agent's account: `gh auth status` is OK, `git push` to that repo
  works with no prompt, and commits carry the right author.
- Deny, expiry and disconnect each leave the UI in a correct state the user can recover from.

## 2. Where it lives, and why

Everything is in `runtime/`. The host is not touched: it holds no knowledge of what the
runtime installs, and nothing here crosses the host/runtime seam. All new routes are
additive on the shared router, so `API_VERSION` does not change. **No host release.**

| Piece | Location | Why there |
|---|---|---|
| GitHub HTTP, identity, clone argv | `runtime/eggie_api/core/github.py` | Stdlib client shaped like `core/cloud.py` |
| Device flow, token/desired/applied files, setup state | `runtime/eggie_api/core/github_link.py` | Platform-free logic next to its twin `core/account.py` |
| Routes `/github*` | `runtime/eggie_api/routes/app.py` | One router, mounted at `/` (bearer) and `/api` (cookie) |
| Per-account `gh`/git setup | `runtime/install/lib/github-apply.sh`, run as root by systemd | The API is uid 1000 in a container and cannot write to user homes |
| systemd units | `runtime/install/systemd/eggie-github.{path,service}`, installed by `install.sh` | Same release path as the rest of the guest setup |
| `git` in the API image | `runtime/eggie_api/Dockerfile` | The clone runs in the API's job registry |
| Console screens | `runtime/web/apps/console/src/github/`, `screens/github/` | Mirrors `account/` and `screens/account/` |
| Agent rule | `runtime/instructions/eggie.md` | Read by Claude Code (`/etc/claude-code/CLAUDE.md`) and Codex |

### Why a systemd path unit (approach A)

Only root can run `gh auth login` as `root` and as `/home/<user>`. The API cannot do that
from its container. The alternatives were entering the VM's namespaces from a privileged
container (`nsenter -t 1`) or a system-wide `GH_TOKEN`. The first gives the API a standing
way into the host. The second puts the token in every process's environment. A path unit
keeps the root work in one short shell script. It is tested the same way as
`install-agents.sh`, and the only input it trusts is a file inside `/opt/eggie`.

### systemd is PID 1 on both platforms

- WSL2: `Wsl2Provider.create()` writes `[boot] systemd=true` to `/etc/wsl.conf` and
  restarts the distro. `install.sh` already needs this for `systemctl enable --now docker`.
- Lima: the Ubuntu 24.04 cloud image boots systemd as PID 1. This is the default and
  `eggie.yaml` does not change it.
- If systemd is not running, `install.sh` already fails at step 2, so no VM reaches this
  feature without it. If the units are missing (a VM on an older runtime), the timeout in
  §5.4 reports it. The live acceptance run checks `ps -p 1 -o comm=` = `systemd` on both
  platforms.

## 3. Identity

- **Scopes:** `repo read:org workflow`.
- **`user.name`:** the GitHub profile `name`, or the `login` when that is empty.
- **`user.email`:** the profile's public `email` from `GET /user`, or
  `<id>+<login>@users.noreply.github.com` when there is no public email. GitHub links
  both addresses to the profile, and the noreply address never trips GH007 on push.
- Both come from the GitHub account alone. The Eggie login plays no part in the git
  identity. They are read at connect time and on every re-apply (§5.5).

## 4. Configuration

- `EGGIE_GITHUB_CLIENT_ID` env var, defaulting to Eggie's OAuth App `client_id`, `Ov23lie5k9VqSCKI52Ci`. The
  default is a constant in `core/constants.py`. It is set through
  `ApiConfig.github_client_id` like every other `EGGIE_*` value, and `stack.yml` passes
  it through with the same default. No client secret exists anywhere. The Device Flow
  needs only the `client_id`, and the App must have "Enable Device Flow" ticked.
- `EGGIE_GITHUB_URL` / `EGGIE_GITHUB_API_URL` (defaults `https://github.com`,
  `https://api.github.com`) exist only so tests and forks point elsewhere.

## 5. Runtime API

### 5.1 `core/github.py`

- `GitHub` is a stdlib `urllib` client with an injectable `opener`, shaped like
  `core/cloud.py`:
  - `device_code(client_id, scope)` → `POST {github}/login/device/code`
  - `device_token(client_id, device_code)` → `POST {github}/login/oauth/access_token`
    with `grant_type=urn:ietf:params:oauth:grant-type:device_code`
  - `user(token)` → `GET /user`
  - `repos(token, page)` → `GET /user/repos?sort=updated&per_page=50&page=n&affiliation=owner,collaborator,organization_member`

  Every call sends `Accept: application/json`. GitHub answers device-token errors with
  **HTTP 200 and `{"error": ...}`**, so the client turns that body into
  `GitHubError(code)`. A 401 becomes `GitHubError("bad_credentials")`, and a network
  failure becomes `GitHubUnavailable`.
- `GitHubLink` is the state machine and poller. It follows `Account`: a daemon poller,
  injectable `clock`/`sleep`/`spawn`, write lock, and a stale-reply check after every
  network call.
  - `authorization_pending` → wait `interval`.
  - `slow_down` → `interval += 5` (or the `interval` GitHub returns, if larger).
  - `expired_token` or a local expiry → `disconnected`, `error: "expired_token"`.
  - `access_denied` → `disconnected`, `error: "access_denied"`.
  - Any other error code → `disconnected`, `error: "github_error"`.
  - Network failure → keep polling until the code expires.
  - On a token: `user()`. Store the identity, write the token, write the
    desired state, bump the generation.

### 5.2 Storage

| What | Where | Mode |
|---|---|---|
| Pending device code, user code, expiry | memory only | — |
| Token | `/opt/eggie/github/token` | `0600`, uid 1000 (API) |
| Identity + bookkeeping (`login`, `gh_id`, `name`, `email`, `generation`, `desired_at`, `needs_reconnect`, `last_error`, `checked_at`) | new `github` row in `state.db` (added in `migrate.py`) | as `state.db` |
| Desired state for the root script | `/opt/eggie/github/desired.json` | `0640`, uid 1000, group docker |
| Result from the root script | `/opt/eggie/github/applied.json` | `0644`, root |

- The device code is kept in memory only. Together with the public `client_id` it is
  enough to get the token. If the API restarts mid-flow, the user just clicks Connect again.
- `install.sh` creates `/opt/eggie/github` as `root:docker`, mode `2770`.
- The token and `desired.json` are written to a temp file in the same directory, then
  `os.replace`d into place, with the mode set at creation (`os.open(..., 0o600)`).
- Docker-group members are root-equivalent in this VM anyway. `0600` keeps the token away
  from everything else.

### 5.3 The desired-state contract

`desired.json` never contains the token:

```json
{"generation": 7, "state": "connected", "login": "octo", "name": "Octo Cat",
 "email": "12345+octo@users.noreply.github.com"}
```

`{"generation": 8, "state": "disconnected"}` on disconnect.

`generation` only increases and is stored in `state.db`. The token is written **before**
`desired.json`, so a script that sees `connected` always finds the token for that
generation.

`applied.json`:

```json
{"generation": 7, "ok": true, "error": null,
 "accounts": [{"name": "root", "ok": true}, {"name": "ihor", "ok": true}]}
```

`github-apply.sh` writes `{"generation": 0, "ok": true, "accounts": []}` on its first run
at install, when no `desired.json` exists. Its presence therefore shows that this VM's
runtime can apply.

### 5.4 Status and setup progress

`GET /github`:

```
{state: "disconnected", error: null | "access_denied" | "expired_token" | "github_error"}
{state: "pending", user_code, url: "https://github.com/login/device", expires_at}
{state: "connected", login, name, email, setup: "applying" | "ready" | "failed" | "runtime_outdated", setup_error}
{state: "needs_reconnect", login}
```

`setup` compares `applied.generation` with the stored `generation`:

- **equal and `ok`** → `ready`
- **equal and not `ok`** → `failed`, `setup_error` = the script's `error` (no token)
- **behind, and `now - desired_at < SETUP_TIMEOUT` (30 s)** → `applying`
- **behind after the timeout, `applied.json` missing** → `runtime_outdated`. The UI says
  "This machine's Eggie runtime needs an update before GitHub can be set up", and the
  host app's Repair re-runs `get.sh`.
- **behind after the timeout, `applied.json` present** → `failed`, `setup_error:
  "setup_timeout"`, message "GitHub setup inside the machine did not finish".

**Token health.** `GET /github` re-checks `GET /user` at most every 5 minutes
(`checked_at`). A `bad_credentials` there, or on any repos or clone call, sets
`needs_reconnect`. It keeps the login and drops the token. It does **not** bump the
generation: the accounts still hold the dead token until the user reconnects or
disconnects. A network failure is not a reconnect.

### 5.5 Routes

All routes are on the shared router, so they are behind the bearer token at `/` and behind
the cookie at `/api`. None of them ever returns the token.

- `GET /github` — status, as above.
- `POST /github/connect` — starts or resumes a device flow. Returns the `pending` status.
  From `connected` it returns the status unchanged. From `needs_reconnect` it starts a new
  flow.
- `POST /github/disconnect` — cancels any pending flow, deletes the token, clears the
  identity, writes `{"state": "disconnected"}` with a new generation, and returns the
  status. The UI then tells the user they can fully revoke access in GitHub → Settings →
  Applications → Authorized OAuth Apps.
- `POST /github/reapply` — rewrites `desired.json` with a new generation and a
  re-read `GET /user`, so a changed profile name or public email is picked up. This is the "Try again" button on `failed` / `runtime_outdated`.
- `GET /github/repos?page=n` — `{repos: [{full_name, private, description, updated_at}],
  has_more}`. `has_more` comes from the `Link: rel="next"` header. Returns 409
  `github_not_connected` when not connected.
- `POST /github/clone` — body `{repo: "owner/name", id?: string}`. Returns 202 with a job.
  - `repo` must match `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`, else 422.
  - `id` defaults to the repo name, run through `_slug`. A taken id is a 409
    `project_exists`, as for `POST /projects`.

### 5.6 The clone job

The clone runs in the existing job registry as `runner.exec` with an explicit env.
The repo is cloned into a hidden staging folder `projects_root/.clone-<hex>` first; if
the project name is still unclaimed when done, it is renamed into place. If another
request (POST /projects, an adopt, a coding agent's own mkdir) claims the name first,
the staging folder is removed and an error is returned; nothing is left behind.

```
sh -c 'umask 002 && exec "$@"' sh \
  git -c credential.helper= \
      -c 'credential.helper=!f() { test "$1" = get && printf "username=x-access-token\npassword=%s\n" "$EGGIE_GH_TOKEN"; }; f' \
      -c core.sharedRepository=group \
      clone -- https://github.com/<owner>/<name>.git /opt/eggie/projects/<id>
env: EGGIE_GH_TOKEN=<token>, GIT_TERMINAL_PROMPT=0
```

- **The token never lands on disk in the repo.** It is only in the child's environment,
  never in argv or the URL. The inline helper is a `-c` for this one command, so nothing
  is written to `.git/config`, and `origin` stays the plain https URL. Later pushes from
  the agent's account go through that account's `gh` credential helper.
- **Why `umask 002` and `core.sharedRepository=group`.** The login accounts are not uid
  1000. On WSL2 there is no login user, so agents run as **root**. On Lima the account
  carries the **macOS uid (usually 501)**. What they share with the API is the docker
  group, which `/opt/eggie/projects` already hands down through setgid. Without these two
  settings the API's default umask 022 produces files the Lima account cannot write, and
  `core.sharedRepository=group` keeps `.git` group-writable whoever writes to it later.
- **Output.** Job stdout/stderr pass through `redact(text, token)` before they are
  stored. git does not print the token, and redacting costs nothing.
- **Success.** Add the project row (the same path as `POST /projects` + `sync.wake()`). If
  a compose file exists, the same job runs `up`, as `eggie clone` does. Otherwise the
  project is left stopped.
- **Failure.** Remove the partial folder. On `Authentication failed` / 401 / 403, set
  `needs_reconnect`. `LocalRunner.exec` gains an optional `env` keyword to make this
  possible (§8).

`git clone` refuses a repo owned by another uid ("dubious ownership") unless
`safe.directory` allows it. Ubuntu 24.04 ships git 2.43, which has no prefix wildcard.
`install.sh` therefore adds `safe.directory = *` to `/etc/gitconfig`, once. Every account
here is in the docker group and root-equivalent, so the ownership check protects nothing
in this VM. This also fixes a hidden bug that exists today: an uploaded folder containing
`.git` is owned by uid 1000 as well.

## 6. Root side: `github-apply.sh`

`eggie-github.path` has `PathChanged=/opt/eggie/github/desired.json`. systemd watches
the parent directory for `IN_MOVED_TO`, so the API's `os.replace` triggers it. It
activates `eggie-github.service`, a root `Type=oneshot` that runs
`/opt/eggie/runtime/install/lib/github-apply.sh`. `install.sh` installs both units,
`systemctl enable --now eggie-github.path`, and runs the script once at the end, so a
repair or a newly added account picks up the current state.

The script is idempotent. It reads `desired.json`, applies it in full, and writes
`applied.json` with the same generation. It loops up to 5 times: a desired.json written
while a pass was running (e.g. a quick connect-then-disconnect) is not covered by
PathChanged firing again after this run exits, so the script re-checks the generation
and applies any new desired state:

```
accounts = root:0:0:/root  +  getent passwd | login-users.sh /etc/shells
loop while generation moves (up to 5 passes):
  for each account:
    as() { runuser -u "$name" -- env HOME="$home" PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
           GH_PROMPT_DISABLED=1 GH_NO_UPDATE_NOTIFIER=1 "$@"; }
    connected:
      as gh auth login --hostname github.com --git-protocol https --insecure-storage --with-token < token
      as gh auth setup-git --hostname github.com
      as git config --global user.name  "$name_from_desired"
      as git config --global user.email "$email_from_desired"
    disconnected:
      as gh auth logout --hostname github.com --user "$recorded_login"   (a "not logged in" exit is fine)
      as git config --global --unset user.name / user.email, only if they still equal
         the values in the previous applied.json (the user's own identity is kept)
```

- **Nothing root-owned in user homes.** Every write in a home goes through `runuser` as
  that account with its own `HOME`, so `~/.config/gh/hosts.yml` and `~/.gitconfig` belong
  to the account.
- **`runuser` without `-i`.** `runuser` without `-l` already carries the caller's
  environment; wiping it here would drop nothing worth dropping. Overriding `HOME` and
  `PATH` keeps `gh`/`git` pointed at the right account.
- **Reading the token.** Root's shell opens the token for the `< token` redirect before
  `runuser` drops privileges. The account never needs read access to
  `/opt/eggie/github/token`.
- **`--insecure-storage`.** It makes the storage deterministic. There is no keyring in a
  headless VM, and gh would fall back to the same file anyway.
- **Logout with `--user`.** Since gh 2.40 one host can hold several accounts (a hand
  sign-in, or a reconnect as someone else). Only log out the account we recorded signing
  in, leaving a hand sign-in alone. Nothing to undo otherwise.
- **Per-account result.** One account failing does not stop the others. `ok` is the AND
  of all accounts, and `error` holds the first failing account's name and gh's stderr.
  gh never echoes the token, but the script passes stderr through the same kind of
  redaction (`sed` on the token value) before writing it.
- **Where `name`/`email` come from.** They are read from `desired.json` with
  `python3 -c 'import json…'`; Ubuntu 24.04 ships python3. Values are passed as argv,
  never interpolated into a shell string.
- **Write order.** `applied.json` is written last, via a temp file and `mv`, so the API
  never reads half a file.

## 7. Console (`runtime/web`)

- **`github/github.ts`** — queries and mutations for the routes above.
  `useGitHubStatus()` polls every 2 s while `pending` or `setup: "applying"`, and
  otherwise not at all. This is what switches the UI to "Connected as @login" without a
  reload.
- **`github/view.ts`** — a pure mapping from status to one screen state, with the copy
  for each:
  - `disconnected` → Connect GitHub
  - `access_denied` → "You said no on GitHub" + Try again
  - `expired_token` → "That code ran out" + Get a new code
  - `github_error` → "GitHub said something unexpected" + Try again
  - `pending` → the code
  - `applying` → "Setting up GitHub inside Eggie…"
  - `ready` → Connected as @login
  - `failed` → the setup error + Try again (`reapply`)
  - `runtime_outdated` → the update message
  - `needs_reconnect` → Reconnect GitHub
- **`screens/github/ConnectModal.tsx`:**
  - Clicking **Connect GitHub** calls `openExternal("https://github.com/login/device")`
    inside the click handler. The URL is fixed and known, so no popup blocker trips.
    Desktop users go through the anchor path, the only one WKWebView hands to the system
    browser.
  - At the same time it `POST`s `/api/github/connect` and shows the `user_code` large,
    with **Copy code**, **Open GitHub again** and the expiry.
- **`screens/github/RepoPicker.tsx`** — the repos list (name, private badge, "updated 3
  days ago" via the existing `projects/format.ts`) and **Load more** while `has_more`.
  Picking a repo calls `POST /api/github/clone`, waits in the modal while the job is in
  its `cloning` phase, then opens the project page, which shows the rest of the job. The
  project row is only added once the clone has landed, so a failed clone leaves nothing
  behind and the error stays in the modal.
- **Entry points:**
  - The empty-state "From GitHub" card: drop "Soon", enable the button.
  - A "From GitHub" button in the list header.
  - A GitHub line in `AccountMenu`: "Connected as @login" + Disconnect, or "Reconnect
    GitHub". Disconnect confirms, then shows the revoke hint from §5.5.
- **Mocks** gain `?scenario=github-pending|github-denied|github-expired|github-applying|github-outdated|github-reconnect`.

## 8. Other changes

- **`LocalRunner.exec(argv, *, root=False, env=None)`** — the new `env` keyword is merged
  over `os.environ`. The host's `VmProvider.exec` does not change: the runtime runner was
  only duck-typed to it for `lifecycle.py`, which does not pass `env`.
- **Dockerfile** — `apt-get install -y --no-install-recommends git ca-certificates`.
- **`install.sh`:**
  - create `/opt/eggie/github` (`root:docker 2770`)
  - install and enable the two units
  - `git config --system safe.directory '*'` if it is not there already
  - run `github-apply.sh` after the per-account loop, before the marker
- **`runtime/instructions/eggie.md`** — the GitHub bullet becomes:
  > GitHub — repositories, pull requests, issues: use `gh` and plain `git` over https.
  > If `gh auth status` fails or a GitHub operation says unauthorized, do **not** run
  > `gh auth login` and do not ask for a token. Tell the user to click **Connect GitHub**
  > in Eggie, then try again.
- **Version** — additive runtime change, `API_VERSION` stays. No bump while 0.2.0 is
  untagged (the highest tag is `runtime-v0.0.5`). Bump `__version__`, `SERVICE_VERSION`
  and both `stack.yml` tags together, then release a `runtime-v*` tag, only if the 0.2.0
  images were already pushed.

## 9. Testing

No test reaches GitHub or the network. HTTP goes through a fake `opener`; shell scripts
run with `bash` against fakes on `PATH`.

- **`core/github.py`, with a fake opener and clock:**
  - `authorization_pending` → keeps polling at `interval`.
  - `slow_down` → the interval grows.
  - `expired_token`, a local expiry and `access_denied` each end `disconnected` with that
    error.
  - A reply for a code cancelled by disconnect mid-poll is dropped.
  - Identity: profile `name` and public `email` are used when set; an empty name gives the
    login and a missing public email gives `<id>+<login>@users.noreply.github.com`.
  - `bad_credentials` on repos → `needs_reconnect`, token gone, generation unchanged.
- **Setup-state mapping** (`generation` × `applied.json` × elapsed time) → `ready` /
  `applying` / `failed` / `setup_timeout` / `runtime_outdated`.
- **The token never escapes.** For every status shape and the repos route, the token
  string is not in the response body. The clone job's stored output is redacted.
- **Clone argv.** The token is not in argv and the URL has no credentials. The env carries
  it and `GIT_TERMINAL_PROMPT=0`. `repo` validation rejects `../x`, `a/b/c` and
  `https://…`.
- **Files.** Token mode `0600`. `desired.json` never contains the token, and its
  generation only increases.
- **`github-apply.sh`** (fake `gh`, `git`, `runuser` on `PATH`, temp homes, fake
  `getent`):
  - connected → login with the token on stdin (not argv), `setup-git`, and the identity
    set per account.
  - disconnected → logout, and the user's own `user.name` is left untouched.
  - One account failing → `ok: false` naming it, the other accounts still applied.
  - `applied.json` echoes the generation.
  - First run with no `desired.json` writes generation 0.
- **`install.sh` text assertions** — units installed and enabled, `safe.directory`, and
  the apply script run before the marker.
- **Console** — a Vitest of `github/view.ts` over every status shape. No component
  snapshots.

Left untested on purpose: the systemd units themselves (declarative, and covered by the
live acceptance run) and the modal components (no logic past `view.ts`).

## 10. Live acceptance (manual, WSL2 and Lima)

1. `ps -p 1 -o comm=` → `systemd`, and `systemctl is-enabled eggie-github.path` →
   `enabled`.
2. Connect → approve → UI reaches "Connected as @login" with no reload. Deny → "You said
   no". Wait out a code → "That code ran out". Each recovers with its button.
3. Pick a private repo → the project appears. `grep -r <token> /opt/eggie/projects/<id>/.git`
   finds nothing, and `git remote -v` is plain https.
4. As the agent's account (root on WSL2, the Lima user on macOS): `gh auth status` is OK;
   `touch`, commit and `git push` succeed with no prompt; `git log -1 --format='%an <%ae>'`
   matches §3; `ls -l ~/.config/gh/hosts.yml ~/.gitconfig` shows the account as owner.
5. Revoke the app on github.com → the UI shows "Reconnect GitHub" within 5 minutes or on
   the next repos call.
6. Disconnect → `gh auth status` fails in every account; the UI shows the revoke hint.
7. On a VM with the previous runtime: Connect → `runtime_outdated` after 30 s.

## 11. Out of scope

- GitHub Enterprise hosts, several GitHub accounts, org SSO authorization prompts beyond
  what `read:org` gives.
- Signing out of Eggie does not disconnect GitHub. They are independent connections.
- Search in the repo picker (paging only).
- SSH remotes.
- The Eggie service gaining a user name.
