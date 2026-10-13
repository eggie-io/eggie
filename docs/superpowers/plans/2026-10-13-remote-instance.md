# Remote Instance (Site Mode) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the runtime run on a service-provisioned server ("site mode"): console at `foo.eggie.io`, projects at `*.foo.eggie.space`, TLS on the server with a service-issued certificate, browser sign-in through a service-minted handoff code.

**Architecture:** Two installer variables (`EGGIE_SITE_HOST`, `EGGIE_SITE_DOMAIN`) persisted to `/opt/eggie/site.env` switch the runtime into site mode. The compose stack stays identical and gains a `stack.site.yml` override that moves Traefik to 80/443 with TLS from a watched directory. The API learns `site_host`, builds `https://` URLs, widens its browser allowlist, seeds its account from a cloud-init file, redeems unknown handoff codes with the service, and keeps the certificate files current from a daily sync pass. The console reads `site` from `/api/health` and hides what does not apply on a server.

**Tech Stack:** bash (installer), Docker Compose + Traefik v3 (edge), Python 3.12 / FastAPI (API), React + Vitest (console), pytest.

**Spec:** `docs/superpowers/specs/2026-10-13-remote-instance-design.md`

## Global Constraints

- Branch `feature/73-remote-instance` (already exists, from `main`). Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Python tests: `TMPDIR=/home/ihor/tmp python3 -m pytest <path> -q` (system `python3`, no `.venv`; `/tmp/pytest-of-$USER` is root-owned here).
- Console tests: `cd runtime/web && npx vitest run <file>`; typecheck with `npm run typecheck` from `runtime/web`.
- Python 3.12+, `from __future__ import annotations`, frozen dataclasses for value types.
- `runtime/` never imports `host/`; no `sys.platform` / `platform.system()` / `os.name` outside `host/providers/`.
- Tests reach repo files via `__file__`, never a cwd-relative path.
- `stack.yml` must stay identical in both modes: only `${VAR:-default}` interpolation may differ (`tests/runtime/test_stack_yml.py` strips interpolation and asserts no literal domain or port remains).
- Install scripts call docker as `/usr/bin/docker`, never bare `docker`.
- No backward-compatibility shims: nothing is deployed in the wild.
- Comments only for edge cases, workarounds or non-obvious logic; never mention tickets or docs.
- Project hosts: `<id>.<domain>` and `<subdomain>--<id>.<domain>` (double hyphen; a slug may contain `--`, so a project id equal to `<service>--<other id>` could collide — accepted, documented in the overlay comment).
- `/opt/eggie/tls` is `2770 root:docker` so the API (docker group) can write and Traefik (root in its container) can read; the spec's `0750` was a slip.

## Review Focus

1. `EGGIE_SITE_HOST=https://foo.eggie.io` or `Foo.Eggie.IO:443` must be refused by `get.sh`, not written into `site.env` and then into a Traefik rule — Task 1 test `test_site_names_must_be_bare_lowercase_hostnames`.
2. A repair or the boot update runs `get.sh` with no site variables; `site.env` must survive, and `install.sh` must re-append the site lines to the `.env` it rewrites — Task 1 test `test_a_run_without_site_variables_keeps_an_existing_site_env`, Task 2 test `test_install_appends_the_site_lines_after_writing_env`.
3. The service is unreachable during first boot; the certificate fetch must retry on the next sync pass, not wait a day — Task 11 test `test_a_failed_fetch_is_retried_on_the_next_pass`.
4. The service returns a certificate missing one of the three names; the API must keep what it has — Task 11 test `test_a_certificate_that_misses_a_name_is_refused`.
5. The session cookie must carry `Secure` on a site and must not locally (the local console is plain http and the browser would drop a Secure cookie) — Task 7 tests `test_the_cookie_is_secure_on_a_site` and `test_the_cookie_is_not_secure_locally`.

---

### Task 1: `get.sh` takes the site names and persists them

**Files:**
- Modify: `runtime/install/get.sh` (inside `main()`, right after `flock 9`)
- Test: `tests/runtime/test_get_sh.py`

**Interfaces:**
- Produces: `/opt/eggie/site.env` with two `%q`-quoted lines `EGGIE_SITE_HOST=…` and `EGGIE_SITE_DOMAIN=…`. Written only when both variables are given; a run with neither leaves an existing file alone; one without the other, or a name that is not a bare lowercase hostname, exits 1 before anything else happens.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_get_sh.py`:

```python
def _run_main(tmp_path, root, **env):
    # The whole script with /opt/eggie pointed at a temp root, a fake install.sh
    # that only echoes, and no network: enough to see what main() writes.
    ref = "runtime-v0.1.0"
    tar_path = _archive_with_install_sh(
        tmp_path, ref, '#!/usr/bin/env bash\necho "install.sh args: $*"\n')
    environ = _bin(tmp_path, dpkg="exit 0\n", curl=f"cp '{tar_path}' \"$4\"\n",
                   git=_git_listing(tmp_path, [ref]))
    for name in ("EGGIE_RUNTIME_REF", "EGGIE_RUNTIME_REPAIR", "EGGIE_RUNTIME_API",
                 "EGGIE_RUNTIME_UPDATE", "EGGIE_SITE_HOST", "EGGIE_SITE_DOMAIN"):
        environ.pop(name, None)
    environ.update(env)
    script = GET.read_text().replace("/opt/eggie", str(root))
    return subprocess.run(["bash", "-c", script], env=environ,
                          capture_output=True, text=True)


def test_both_site_names_are_written_to_site_env(tmp_path):
    root = tmp_path / "opt-eggie"
    root.mkdir()
    result = _run_main(tmp_path, root, EGGIE_SITE_HOST="foo.eggie.io",
                       EGGIE_SITE_DOMAIN="foo.eggie.space")
    assert result.returncode == 0, result.stderr
    assert (root / "site.env").read_text() == (
        "EGGIE_SITE_HOST=foo.eggie.io\nEGGIE_SITE_DOMAIN=foo.eggie.space\n")


def test_one_site_name_without_the_other_is_refused_before_installing(tmp_path):
    root = tmp_path / "opt-eggie"
    root.mkdir()
    result = _run_main(tmp_path, root, EGGIE_SITE_HOST="foo.eggie.io")
    assert result.returncode != 0
    assert "EGGIE_SITE_HOST and EGGIE_SITE_DOMAIN" in result.stderr
    assert not (root / "site.env").exists()
    assert "install.sh args" not in result.stdout


@pytest.mark.parametrize("bad", ["https://foo.eggie.io", "foo.eggie.io:443",
                                 "Foo.Eggie.IO", "foo eggie io", "foo"])
def test_site_names_must_be_bare_lowercase_hostnames(tmp_path, bad):
    root = tmp_path / "opt-eggie"
    root.mkdir()
    result = _run_main(tmp_path, root, EGGIE_SITE_HOST=bad,
                       EGGIE_SITE_DOMAIN="foo.eggie.space")
    assert result.returncode != 0
    assert "bare hostnames" in result.stderr
    assert not (root / "site.env").exists()


def test_a_run_without_site_variables_keeps_an_existing_site_env(tmp_path):
    # The boot update and a plain repair run get.sh with no site variables.
    root = tmp_path / "opt-eggie"
    root.mkdir()
    (root / "site.env").write_text("EGGIE_SITE_HOST=foo.eggie.io\nEGGIE_SITE_DOMAIN=foo.eggie.space\n")
    result = _run_main(tmp_path, root)
    assert result.returncode == 0, result.stderr
    assert (root / "site.env").read_text() == (
        "EGGIE_SITE_HOST=foo.eggie.io\nEGGIE_SITE_DOMAIN=foo.eggie.space\n")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_get_sh.py -q -k "site"`
Expected: `test_both_site_names…` and the refusal tests FAIL (no `site.env` written / exit 0 instead of 1); the "keeps an existing" test passes already.

- [ ] **Step 3: Implement in `get.sh`**

In `main()`, directly after the two lines

```bash
  exec 9>/opt/eggie/update.lock
  flock 9
```

insert:

```bash
  # Site mode: a server with its own hostnames. Both names or neither; a run
  # with neither (a repair, the boot update) keeps the site.env an earlier run
  # wrote. Bare lowercase hostnames only -- these land in a Traefik Host rule.
  if [[ -n "${EGGIE_SITE_HOST:-}" || -n "${EGGIE_SITE_DOMAIN:-}" ]]; then
    local site_name
    for site_name in "${EGGIE_SITE_HOST:-}" "${EGGIE_SITE_DOMAIN:-}"; do
      if [[ ! "$site_name" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]]; then
        echo "EGGIE_SITE_HOST and EGGIE_SITE_DOMAIN must both be set, as bare hostnames" \
             "(got '${EGGIE_SITE_HOST:-}' and '${EGGIE_SITE_DOMAIN:-}')" >&2
        exit 1
      fi
    done
    printf 'EGGIE_SITE_HOST=%q\nEGGIE_SITE_DOMAIN=%q\n' \
      "$EGGIE_SITE_HOST" "$EGGIE_SITE_DOMAIN" > /opt/eggie/site.env
  fi
```

Also add `EGGIE_SITE_HOST`, `EGGIE_SITE_DOMAIN` to the env-var list in the "`get.sh` is a live contract" paragraph of `runtime/install/CLAUDE.md` (one line: "`EGGIE_SITE_HOST`/`EGGIE_SITE_DOMAIN` (site mode, both or neither, persisted to `/opt/eggie/site.env`)").

- [ ] **Step 4: Run the tests to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_get_sh.py -q`
Expected: all PASS (including the pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add runtime/install/get.sh runtime/install/CLAUDE.md tests/runtime/test_get_sh.py
git commit -m "get.sh: take the site names and persist them to site.env

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `install.sh` configures site mode

**Files:**
- Modify: `runtime/install/install.sh` (top after `REPAIR` parsing; step 5 `.env`; step 7 compose lines; new step 7a; step 12 `connect.json`)
- Create: `runtime/install/site/tls.yml`
- Test: `tests/runtime/test_install_shell.py`

**Interfaces:**
- Consumes: `/opt/eggie/site.env` from Task 1.
- Produces: in site mode `.env` ends with `EGGIE_SITE_HOST=<host>`, `EGGIE_DOMAIN=<domain>`, `EGGIE_API_BIND=127.0.0.1`; every stack compose command carries `$SITE_STACK` (`-f /opt/eggie/runtime/stack.site.yml` in site mode, empty otherwise); `/opt/eggie/tls/` (`2770 root:docker`) holding `tls.yml`; `connect.json` with `"vm": "site"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_install_shell.py`:

```python
SITE_ENV = f"{constants.GUEST_ROOT}/site.env"


def test_install_reads_site_env_before_writing_the_compose_env():
    commands = _commands()
    assert _index_of(f"source {SITE_ENV}") < _index_of(f"{constants.GUEST_ROOT}/.env")


def test_install_appends_the_site_lines_after_writing_env():
    # .env is rewritten on every run (the docker GID and image version); the
    # site lines must follow in the same run or a repair drops site mode.
    commands = _commands()
    write = _index_of(f"EGGIE_VERSION=%s")
    append = _index_of("EGGIE_API_BIND=127.0.0.1")
    assert write < append < _index_of(" up -d")
    assert any("EGGIE_DOMAIN=%s" in l and ">> " in l for l in commands), \
        "the site domain becomes EGGIE_DOMAIN by appending, never by rewriting"


def test_every_stack_compose_command_carries_the_site_override():
    stack_lines = [l for l in _commands() if f"compose -f {STACK}" in l]
    assert len(stack_lines) >= 3, stack_lines
    assert all("$SITE_STACK" in l for l in stack_lines), stack_lines


def test_the_site_override_is_the_one_shipped_next_to_stack_yml():
    (line,) = [l for l in _commands() if 'SITE_STACK="-f' in l]
    assert line.endswith('stack.site.yml"')
    assert (STACK_YML.parent / "stack.site.yml").is_file()


def test_install_ships_traefik_a_default_certificate_config_in_site_mode():
    match = re.search(r'install -m 644 "\$INSTALL_DIR/(\S+)" /opt/eggie/tls/tls\.yml',
                      INSTALL.read_text())
    assert match, "install.sh does not install /opt/eggie/tls/tls.yml"
    doc = yaml.safe_load((INSTALL.parent / match.group(1)).read_text())
    cert = doc["tls"]["stores"]["default"]["defaultCertificate"]
    assert cert == {"certFile": "/opt/eggie/tls/site.crt", "keyFile": "/opt/eggie/tls/site.key"}
    # The API (docker group) writes the files; Traefik reads the directory.
    assert "install -d -m 2770 -o root -g docker /opt/eggie/tls" in INSTALL.read_text()


def test_install_records_a_site_as_its_own_vm_kind():
    text = INSTALL.read_text()
    assert "vm=site" in text
    assert text.index("vm=site") < text.index("/opt/eggie/connect.json")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_install_shell.py -q -k "site"`
Expected: 6 FAIL (`AssertionError: install.sh has no line containing …`).

- [ ] **Step 3: Create `runtime/install/site/tls.yml`**

```yaml
# Traefik dynamic config for a site: the certificate the API fetches from the
# service. Until site.crt exists Traefik serves its own self-signed one and
# reloads the moment the file lands (the directory is watched).
tls:
  stores:
    default:
      defaultCertificate:
        certFile: /opt/eggie/tls/site.crt
        keyFile: /opt/eggie/tls/site.key
```

- [ ] **Step 4: Edit `install.sh`**

(a) After the `REPAIR` block (right after `fi` following `REPAIR=1`), add:

```bash
# Site mode: a server with its own hostnames; get.sh wrote the file. The
# override puts Traefik on 443 with TLS; `$SITE_STACK` is unquoted on purpose
# so an empty value adds nothing.
SITE=0
SITE_STACK=""
# shellcheck disable=SC1091
if [[ -s /opt/eggie/site.env ]]; then
  source /opt/eggie/site.env
  SITE=1
  SITE_STACK="-f $RUNTIME_DIR/stack.site.yml"
fi
```

(b) After the line `printf 'EGGIE_DOCKER_GID=%s\nEGGIE_VERSION=%s\n' "$DOCKER_GID" "$IMAGE_VERSION" > /opt/eggie/.env`, add:

```bash
if (( SITE )); then
  # The console answers on the site host, projects live under the site
  # domain, and the bearer port stays off the public interface.
  printf 'EGGIE_SITE_HOST=%s\nEGGIE_DOMAIN=%s\nEGGIE_API_BIND=127.0.0.1\n' "$EGGIE_SITE_HOST" "$EGGIE_SITE_DOMAIN" >> /opt/eggie/.env
fi
```

(c) Change the three stack compose commands:

```bash
if ! /usr/bin/docker compose -f /opt/eggie/stack.yml $SITE_STACK --profile tunnel pull 2>&1 | tee "$PULL_LOG"; then
…
/usr/bin/docker compose -f /opt/eggie/stack.yml $SITE_STACK up -d
…
  /usr/bin/docker compose -f /opt/eggie/stack.yml $SITE_STACK up -d --force-recreate api
```

(d) Between `install -m 644 "$RUNTIME_DIR/stack.yml" /opt/eggie/stack.yml` and the `PULL_LOG` line, add:

```bash
# 7a. site mode: where the API puts the certificate and Traefik reads it.
if (( SITE )); then
  install -d -m 2770 -o root -g docker /opt/eggie/tls
  install -m 644 "$INSTALL_DIR/site/tls.yml" /opt/eggie/tls/tls.yml
fi
```

(e) Step 12 becomes:

```bash
if (( SITE )); then vm=site
elif grep -qi microsoft /proc/sys/kernel/osrelease; then vm=wsl
elif [[ -d /mnt/lima-cidata ]]; then vm=lima
else vm=other
fi
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_install_shell.py -q`
Expected: all PASS. `bash -n runtime/install/install.sh` exits 0.

- [ ] **Step 6: Commit**

```bash
git add runtime/install/install.sh runtime/install/site/tls.yml tests/runtime/test_install_shell.py
git commit -m "install.sh: site mode from site.env, tls directory, site override, vm kind

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: the edge — `stack.yml` variables and `stack.site.yml`

**Files:**
- Modify: `runtime/stack.yml` (api `ports`, api and web router rules)
- Create: `runtime/stack.site.yml`
- Test: `tests/runtime/test_stack_yml.py`

**Interfaces:**
- Consumes: `EGGIE_SITE_HOST`, `EGGIE_API_BIND` from `.env` (Task 2).
- Produces: the console and `/api` routers also match `Host(<site host>)`; the api service publishes on `${EGGIE_API_BIND:-0.0.0.0}`; the override moves Traefik's `web` entrypoint to 443 with TLS and adds a redirecting `http` entrypoint on 80 plus a watched file provider on `/opt/eggie/tls`.

- [ ] **Step 1: Write the failing tests**

In `tests/runtime/test_stack_yml.py`, replace `test_the_api_service_binds_the_port_it_is_published_on` with:

```python
def test_the_api_service_binds_the_port_it_is_published_on():
    # ApiConfig already reads EGGIE_API_PORT; publishing a fixed port
    # while the process binds a configured one forwards the host to nothing.
    # The bind address is a variable too: a site keeps the bearer port on
    # loopback, WSL2 needs all interfaces for localhostForwarding.
    api = yaml.safe_load(_text())["services"]["api"]
    assert api["ports"] == ["${EGGIE_API_BIND:-0.0.0.0}:${EGGIE_API_PORT:-39099}:"
                            "${EGGIE_API_PORT:-39099}"]
    assert "EGGIE_API_PORT=${EGGIE_API_PORT:-39099}" in api["environment"]
```

and append:

```python
import shutil
import subprocess

SITE_STACK = STACK.with_name("stack.site.yml")


def test_the_console_routers_also_answer_on_the_site_host():
    services = yaml.safe_load(_text())["services"]
    for name, router in (("web", "eggie-web"), ("api", "eggie-api")):
        rule = _labels(services[name])[f"traefik.http.routers.{router}.rule"]
        assert "Host(`${EGGIE_SITE_HOST:-localhost}`)" in rule, rule
        assert "Host(`localhost`)" in rule and "Host(`127.0.0.1`)" in rule


def test_the_site_override_moves_the_web_entrypoint_to_443_with_tls():
    # Keeping the entrypoint name `web` is what lets every router label in the
    # stack and in the project overlay stay untouched.
    traefik = yaml.safe_load(SITE_STACK.read_text())["services"]["traefik"]
    command = traefik["command"]
    assert "--entryPoints.web.address=:443" in command
    assert "--entryPoints.web.http.tls=true" in command
    assert "--entryPoints.http.address=:80" in command
    assert "--entryPoints.http.http.redirections.entryPoint.to=web" in command
    assert "--providers.file.directory=/opt/eggie/tls" in command
    assert "--providers.file.watch=true" in command
    # compose replaces `command` wholesale, so the base flags must be repeated.
    base = yaml.safe_load(_text())["services"]["traefik"]["command"]
    for flag in base:
        if "entryPoints.web.address" in flag:
            continue
        assert flag in command, f"base flag dropped by the override: {flag}"
    assert traefik["ports"] == ["80:80", "443:443"]
    assert "/opt/eggie/tls:/opt/eggie/tls:ro" in traefik["volumes"]
    assert set(yaml.safe_load(SITE_STACK.read_text())["services"]) == {"traefik"}


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose not installed")
def test_the_merged_site_stack_publishes_only_the_edge_ports_to_the_world(tmp_path):
    env = tmp_path / ".env"
    env.write_text("EGGIE_DOCKER_GID=999\nEGGIE_VERSION=0.0.0\nEGGIE_SITE_HOST=foo.eggie.io\n"
                   "EGGIE_DOMAIN=foo.eggie.space\nEGGIE_API_BIND=127.0.0.1\n")
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env), "-f", str(STACK), "-f", str(SITE_STACK),
         "config", "--format", "json"], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"docker compose config unavailable: {result.stderr.strip()[:120]}")
    import json
    services = json.loads(result.stdout)["services"]
    published = {(p.get("host_ip", ""), p["published"]) for p in services["traefik"]["ports"]}
    assert {("", "80"), ("", "443")} <= published
    (api_port,) = services["api"]["ports"]
    assert api_port["host_ip"] == "127.0.0.1"
    rule = services["web"]["labels"]["traefik.http.routers.eggie-web.rule"]
    assert "Host(`foo.eggie.io`)" in rule
```

Add `import pytest` at the top of the file if absent.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_stack_yml.py -q`
Expected: the three new/changed tests FAIL (`FileNotFoundError` for the override, assertion on `ports`).

- [ ] **Step 3: Edit `runtime/stack.yml`**

api service `ports`:

```yaml
    ports:
      - "${EGGIE_API_BIND:-0.0.0.0}:${EGGIE_API_PORT:-39099}:${EGGIE_API_PORT:-39099}"
```

and extend the comment above it with one line: `# EGGIE_API_BIND: a site keeps this on 127.0.0.1; the guest CLI dials loopback either way.`

api labels:

```yaml
      - "traefik.http.routers.eggie-api.rule=(Host(`localhost`) || Host(`127.0.0.1`) || Host(`${EGGIE_SITE_HOST:-localhost}`)) && PathPrefix(`/api`)"
```

web labels:

```yaml
      - "traefik.http.routers.eggie-web.rule=Host(`localhost`) || Host(`127.0.0.1`) || Host(`${EGGIE_SITE_HOST:-localhost}`)"
```

- [ ] **Step 4: Create `runtime/stack.site.yml`**

```yaml
# Compose override for a site (a server with its own hostnames). The base
# stack.yml is unchanged; install.sh adds this file to every compose command
# when /opt/eggie/site.env exists:
#
#   docker compose -f /opt/eggie/stack.yml -f /opt/eggie/runtime/stack.site.yml up -d
#
# The `web` entrypoint keeps its name and moves to 443 with TLS, so no router
# label in the stack or in a project overlay changes. `command` is replaced
# wholesale by compose, hence the repeated base flags; `ports` and `volumes`
# are appended. The certificate comes from the API (services/certificate.py)
# into /opt/eggie/tls, which Traefik watches; tls.yml there names the files.
services:
  traefik:
    command:
      - "--entryPoints.http.address=:80"
      - "--entryPoints.http.http.redirections.entryPoint.to=web"
      - "--entryPoints.http.http.redirections.entryPoint.scheme=https"
      - "--entryPoints.web.address=:443"
      - "--entryPoints.web.http.tls=true"
      - "--entryPoints.web.transport.respondingTimeouts.readTimeout=30m"
      - "--providers.docker=true"
      - "--providers.docker.exposedByDefault=false"
      - "--providers.docker.network=edge"
      - "--providers.file.directory=/opt/eggie/tls"
      - "--providers.file.watch=true"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /opt/eggie/tls:/opt/eggie/tls:ro
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_stack_yml.py tests/runtime/test_install_shell.py -q`
Expected: all PASS (the merged-config test may skip if docker is absent; it must pass where docker runs).

- [ ] **Step 6: Commit**

```bash
git add runtime/stack.yml runtime/stack.site.yml tests/runtime/test_stack_yml.py
git commit -m "Stack: site host on the console routers, api bind address, stack.site.yml for TLS on 443

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `snapshot-prep.sh`

**Files:**
- Create: `runtime/install/snapshot-prep.sh`
- Test: `tests/runtime/test_snapshot_prep.py`

**Interfaces:**
- Produces: a script run as root that stops the stack and removes `/opt/eggie/{api.token,state.db,state.db-wal,state.db-shm,connect.json,site.env,account.json}` and the directories `tls`, `tunnel`, `uploads`, then runs `cloud-init clean --logs`. Keeps `runtime/`, `.env`, `runtime.env`, `runtime.version`, `stack.yml`, `projects/`.

- [ ] **Step 1: Write the failing test**

Create `tests/runtime/test_snapshot_prep.py`:

```python
"""snapshot-prep.sh strips what makes an installed runtime one machine's, so a
server image made from it gives every droplet its own token, database and
certificate. Runs under real bash with docker and cloud-init faked."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "snapshot-prep.sh"

PER_INSTANCE = ["api.token", "state.db", "state.db-wal", "connect.json", "site.env",
                "account.json", "tls/site.crt", "tls/site.key", "tls/tls.yml",
                "tunnel/token", "uploads/partial"]
KEPT = ["runtime/install/install.sh", ".env", "runtime.env", "runtime.version",
        "stack.yml", "projects/blog/docker-compose.yml"]


def _run(tmp_path: Path):
    root = tmp_path / "opt-eggie"
    for rel in PER_INSTANCE + KEPT:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("docker", "cloud-init"):
        fake = bin_dir / name
        fake.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
        fake.chmod(0o755)
    script = SCRIPT.read_text().replace("/opt/eggie", str(root)).replace(
        "/usr/bin/docker", str(bin_dir / "docker"))
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    return result, root, log.read_text() if log.exists() else ""


def test_prep_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_prep_removes_every_per_instance_file_and_keeps_the_runtime(tmp_path):
    result, root, calls = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    for rel in PER_INSTANCE:
        assert not (root / rel).exists(), rel
    for rel in KEPT:
        assert (root / rel).exists(), rel


def test_prep_stops_the_stack_and_resets_cloud_init(tmp_path):
    # A container left running would come back at boot before install.sh
    # reconfigures it; cloud-init must run user data again on the new droplet.
    _, _, calls = _run(tmp_path)
    assert "compose" in calls and " down" in calls
    assert "cloud-init clean --logs" in calls
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_snapshot_prep.py -q`
Expected: FAIL (`FileNotFoundError` reading the script).

- [ ] **Step 3: Create `runtime/install/snapshot-prep.sh`**

```bash
#!/usr/bin/env bash
# Run as root before imaging a server. Strips what makes an installed runtime
# this machine's: every droplet made from the image must start with its own
# API token, database, certificate and cloud-init run. The runtime tree,
# the images, .env and runtime.env stay; the next boot's get.sh (repair mode,
# with the site names) finishes the job.
set -euo pipefail

ROOT=/opt/eggie

# `down`, not `stop`: a stopped container with `restart: always` is restarted
# by dockerd at boot, before cloud-init has configured anything.
if [[ -f "$ROOT/stack.yml" ]]; then
  /usr/bin/docker compose -f "$ROOT/stack.yml" --profile tunnel down --remove-orphans
fi
rm -f "$ROOT/api.token" "$ROOT/state.db" "$ROOT/state.db-wal" "$ROOT/state.db-shm" \
      "$ROOT/connect.json" "$ROOT/site.env" "$ROOT/account.json"
rm -rf "$ROOT/tls" "$ROOT/tunnel" "$ROOT/uploads"
cloud-init clean --logs
echo "ready to snapshot: power off and take the image"
```

`chmod +x runtime/install/snapshot-prep.sh`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/test_snapshot_prep.py -q`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/install/snapshot-prep.sh tests/runtime/test_snapshot_prep.py
git commit -m "Add snapshot-prep.sh: strip per-instance state before imaging a server

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: one label per project host

**Files:**
- Modify: `runtime/eggie_api/domain/overlay.py:9-12`
- Test: `tests/runtime/api/test_overlay.py`, plus any test asserting the old `api.myproj` shape

**Interfaces:**
- Produces: `host_for(project_id, web, domain)` → `f"{sub}--{id}.{domain}"` for a web with a subdomain, `f"{id}.{domain}"` otherwise.

- [ ] **Step 1: Change the failing test**

In `tests/runtime/api/test_overlay.py` replace `test_host_primary_is_bare_id_additional_prefixed`:

```python
def test_host_primary_is_bare_id_additional_prefixed_one_label_deep():
    # A site's wildcard certificate covers `*.<domain>` only; `api.myproj.d.io`
    # would fall outside it, so additional services share the label.
    assert host_for("myproj", WebSpec("app", 80), "d.io") == "myproj.d.io"
    assert host_for("myproj", WebSpec("api", 4000, subdomain="api"), "d.io") == "api--myproj.d.io"
```

- [ ] **Step 2: Run to verify it fails**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_overlay.py -q`
Expected: 1 FAIL (`'api.myproj.d.io' == 'api--myproj.d.io'`).

- [ ] **Step 3: Implement**

`runtime/eggie_api/domain/overlay.py`:

```python
def host_for(project_id: str, web: WebSpec, domain: str) -> str:
    # One label under the domain: a site's wildcard certificate covers
    # `*.<domain>` only. `--` rather than `-`: slugs can contain single
    # hyphens, so `web-bar` could be a project of its own.
    if web.subdomain:
        return f"{web.subdomain}--{project_id}.{domain}"
    return f"{project_id}.{domain}"
```

- [ ] **Step 4: Run the suite and fix expectations of the old shape**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime -q`
Expected: one more failure, `test_overlay.py::test_two_web_services_get_distinct_hosts_and_routers`, which asserts the derived `api.myproj.d.io` host rule; change that literal to `api--myproj.d.io`. The `api.blog.d.io` literals in `test_public.py` and `test_cloud.py` are hand-built service fixtures, not derived from `host_for`; leave them. Then all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/domain/overlay.py tests/runtime
git commit -m "Project hosts one label deep: <service>--<id>.<domain>

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `ApiConfig.site_host`, `public_url`, site URLs

**Files:**
- Modify: `runtime/eggie_api/config.py`, `runtime/eggie_api/services/loader.py:66-81`
- Modify: `tests/runtime/api/conftest.py` (extract `_make_env`, add `site_env`)
- Test: `tests/runtime/api/test_site.py` (new)

**Interfaces:**
- Produces: `ApiConfig.site_host: str | None`, `ApiConfig.tls_dir: Path`, `ApiConfig.account_seed: Path`, `ApiConfig.public_url(host) -> str`; env `EGGIE_SITE_HOST`, `EGGIE_TLS_DIR`, `EGGIE_ACCOUNT_SEED`. The `site_env` fixture: like `env` but `site_host="foo.eggie.io"`, `domain="foo.eggie.space"`. Later tasks use `SITE_BROWSER = {"Host": "foo.eggie.io"}` and `SITE_ORIGIN = {"Origin": "https://foo.eggie.io"}` from `conftest`.

- [ ] **Step 1: Refactor the fixture and write the failing tests**

In `tests/runtime/api/conftest.py` replace the `env` fixture with:

```python
SITE_BROWSER = {"Host": "foo.eggie.io"}
SITE_ORIGIN = {"Origin": "https://foo.eggie.io"}


def _make_env(tmp_path, **overrides):
    token_path = tmp_path / "api.token"
    token_path.write_text("test-token")
    config = ApiConfig(
        domain="test.local",
        edge_port=41080,
        projects_root=tmp_path / "projects",
        state_db=tmp_path / "state.db",
        version="9.9.9",
        token_path=token_path,
        connect_path=tmp_path / "connect.json",
        uploads_root=tmp_path / "uploads",
        agent_status_dir=tmp_path / "agent-status",
        agents_dir=tmp_path / "agents",
        # No retry window: these tests assert on the verdict, and the window
        # itself is covered against a fake clock in test_health.py.
        ready_timeout=0.0,
        stack_file=tmp_path / "stack.yml",
        tunnel_token_path=tmp_path / "tunnel.token",
        tls_dir=tmp_path / "tls",
        account_seed=tmp_path / "account.json",
        **overrides,
    )
    runner = FakeRunner()
    probe = FakeProbe()
    app = create_app(config=config, runner=runner, http_probe=probe)
    with TestClient(app, headers=AUTH) as client:
        # raw_client returns the 500 a real caller would see instead of
        # re-raising the exception inside the test.
        yield SimpleNamespace(client=client, config=config, runner=runner,
                              probe=probe, repos=app.state.services.repos, jobs=app.state.services.jobs,
                              raw_client=TestClient(app, raise_server_exceptions=False,
                                                    headers=AUTH),
                              app=app)


@pytest.fixture
def env(tmp_path):
    yield from _make_env(tmp_path)


@pytest.fixture
def site_env(tmp_path):
    # A server with its own hostnames: console on site_host, projects under domain.
    yield from _make_env(tmp_path, site_host="foo.eggie.io", domain="foo.eggie.space")
```

Create `tests/runtime/api/test_site.py`:

```python
"""What changes when the API runs on a site (a server with its own hostnames)."""
from eggie_api.config import ApiConfig
from tests.runtime.api.conftest import COMPOSE_ONE_WEB, _write_compose


def test_site_host_comes_from_the_environment_and_is_absent_locally():
    assert ApiConfig.from_env({}).site_host is None
    assert ApiConfig.from_env({"EGGIE_SITE_HOST": ""}).site_host is None
    assert ApiConfig.from_env({"EGGIE_SITE_HOST": "foo.eggie.io"}).site_host == "foo.eggie.io"


def test_public_url_has_no_port_and_https_on_a_site():
    local = ApiConfig(domain="d.io", edge_port=41080)
    assert local.public_url("blog.d.io") == "http://blog.d.io:41080"
    site = ApiConfig(domain="foo.eggie.space", site_host="foo.eggie.io")
    assert site.public_url("blog.foo.eggie.space") == "https://blog.foo.eggie.space"


def test_project_urls_on_a_site_are_https_under_the_site_domain(site_env):
    site_env.client.post("/projects", json={"id": "blog"})
    _write_compose(site_env, "blog", COMPOSE_ONE_WEB)
    project = site_env.client.get("/projects/blog").json()
    assert project["urls"] == ["https://blog.foo.eggie.space"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_site.py -q`
Expected: errors — `ApiConfig.__init__() got an unexpected keyword argument 'tls_dir'`.

- [ ] **Step 3: Implement**

`runtime/eggie_api/config.py` — add fields after `agents_dir`:

```python
    # A server with its own hostnames ("site mode"): the console's host.
    # Projects live under `domain`, which the installer sets alongside it.
    site_host: str | None = None
    # Where the service-issued certificate lands; Traefik watches it.
    tls_dir: Path = Path(f"{constants.GUEST_ROOT}/tls")
    # A token pair the service leaves before first boot, imported once.
    account_seed: Path = Path(f"{constants.GUEST_ROOT}/account.json")
```

and the method:

```python
    def public_url(self, host: str) -> str:
        """Where a browser reaches `host` through the edge."""
        if self.site_host:
            return f"https://{host}"
        return f"http://{host}:{self.edge_port}"
```

In `from_env`, before `version=`:

```python
            site_host=env.get("EGGIE_SITE_HOST") or None,
            tls_dir=Path(env.get("EGGIE_TLS_DIR", f"{constants.GUEST_ROOT}/tls")),
            account_seed=Path(env.get("EGGIE_ACCOUNT_SEED",
                                      f"{constants.GUEST_ROOT}/account.json")),
```

`runtime/eggie_api/services/loader.py`:

```python
    def urls_for(self, project: Project, domain: str) -> list[str]:
        return [self._config.public_url(host_for(project.id, web, domain))
                for web in project.webs]
```

and in `public_hosts`:

```python
        return [{"service": web.service, "hostname": host,
                 "local_url": self._config.public_url(host)}
                for web, host in zip(project.webs, hosts)]
```

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/config.py runtime/eggie_api/services/loader.py tests/runtime/api/conftest.py tests/runtime/api/test_site.py
git commit -m "API: site_host config and https project URLs on a site

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: browser allowlist and Secure cookie on a site

**Files:**
- Modify: `runtime/eggie_api/rest/auth.py`, `runtime/eggie_api/rest/routers/sessions.py`
- Test: `tests/runtime/api/test_api_browser_auth.py`, `tests/runtime/api/test_sessions.py`

**Interfaces:**
- Produces: `auth.cookie_options(config) -> dict` (`max_age`, `httponly`, `samesite`, `path`, `secure`); the `/api` mount accepts `Host: <site_host>` and `Origin: https://<site_host>` when `site_host` is set.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/api/test_api_browser_auth.py`:

```python
from tests.runtime.api.conftest import SITE_BROWSER, SITE_ORIGIN


def test_a_site_accepts_its_own_host_without_a_port(site_env):
    # On 443 the browser sends no port in Host.
    resp = TestClient(site_env.app, headers=SITE_BROWSER).get("/api/health")
    assert resp.status_code == 200


def test_a_site_accepts_writes_from_its_https_origin(site_env):
    resp = TestClient(site_env.app, headers={**SITE_BROWSER, **SITE_ORIGIN}).post(
        "/api/session", json={"code": "nope"})
    # Past the host and origin checks: the code itself is what's refused.
    assert resp.json()["error"]["code"] == "handoff_invalid"


def test_a_site_still_accepts_localhost_and_refuses_other_hosts(site_env):
    assert _browser(site_env).get("/api/health").status_code == 200
    resp = _browser(site_env, Host="bar.foo.eggie.space").get("/api/health")
    assert resp.json()["error"]["code"] == "forbidden_host"


def test_the_site_host_is_not_accepted_locally(env):
    resp = TestClient(env.app, headers=SITE_BROWSER).get("/api/health")
    assert resp.json()["error"]["code"] == "forbidden_host"
```

Append to `tests/runtime/api/test_sessions.py`:

```python
from tests.runtime.api.conftest import SITE_BROWSER, SITE_ORIGIN


def _cookie_header(env, headers):
    code = env.client.post("/sessions/handoff").json()["code"]
    resp = TestClient(env.app, headers=headers).post("/api/session", json={"code": code})
    assert resp.status_code == 200
    return resp.headers["set-cookie"]


def test_the_cookie_is_not_secure_locally(env):
    # The local console is plain http: a Secure cookie would never come back.
    assert "Secure" not in _cookie_header(env, {**BROWSER, **ORIGIN})


def test_the_cookie_is_secure_on_a_site(site_env):
    assert "Secure" in _cookie_header(site_env, {**SITE_BROWSER, **SITE_ORIGIN})
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_api_browser_auth.py tests/runtime/api/test_sessions.py -q`
Expected: the site tests FAIL with `forbidden_host`; the cookie test FAILS on the `Secure` assertion.

- [ ] **Step 3: Implement**

`runtime/eggie_api/rest/auth.py` — add after `read_token`:

```python
def cookie_options(config: ApiConfig) -> dict:
    # Secure only on a site: the local console is plain http on loopback.
    return {"max_age": SESSION_TTL, "httponly": True, "samesite": "strict",
            "path": "/api", "secure": config.site_host is not None}
```

In `install`, replace the two allowlist lines with:

```python
    allowed_hosts = {f"localhost:{config.edge_port}",
                     f"127.0.0.1:{config.edge_port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    if config.site_host:
        # The site's edge is 443, so Host carries no port.
        allowed_hosts.add(config.site_host)
        allowed_origins.add(f"https://{config.site_host}")
    cookie = cookie_options(config)
```

and the slide branch:

```python
                if verdict.extended:
                    response.set_cookie(COOKIE, session_id, **cookie)
```

`runtime/eggie_api/rest/routers/sessions.py`:

```python
from ...services.sessions import COOKIE, HANDOFF_TTL
from ..auth import cookie_options
…
        response.set_cookie(COOKIE, session_id, **cookie_options(s.config))
```

(drop the now-unused `SESSION_TTL` import there.)

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/rest/auth.py runtime/eggie_api/rest/routers/sessions.py tests/runtime/api/test_api_browser_auth.py tests/runtime/api/test_sessions.py
git commit -m "Browser auth: accept the site host and https origin, Secure cookie on a site

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: cloud client calls for the certificate and the handoff

**Files:**
- Modify: `runtime/eggie_api/infra/cloud.py`, `tests/runtime/api/fake_cloud.py`
- Test: `tests/runtime/api/test_cloud.py`

**Interfaces:**
- Produces: `Cloud.instance_certificate(token)` → `GET /v1/instances/me/certificate`; `Cloud.redeem_instance_handoff(token, code)` → `POST /v1/instances/handoff/redeem` `{"code": …}`. `FakeCloud` replays `instance_certificate` and `redeem_instance_handoff`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/api/test_cloud.py`:

```python
def test_the_certificate_is_fetched_from_the_instance_route_with_the_token():
    opener = Opener((200, b'{"names": [], "certificate": "c", "private_key": "k", "not_after": "x"}'))
    out = Cloud("https://svc/api", opener=opener).instance_certificate("at-1")
    (request,) = opener.requests
    assert (request.get_method(), request.full_url) == ("GET", "https://svc/api/v1/instances/me/certificate")
    assert request.get_header("Authorization") == "Bearer at-1"
    assert out["private_key"] == "k"


def test_a_handoff_code_is_redeemed_with_the_token_and_the_code():
    opener = Opener((200, b'{"ok": true}'))
    Cloud("https://svc/api", opener=opener).redeem_instance_handoff("at-1", "k3x9")
    (request,) = opener.requests
    assert (request.get_method(), request.full_url) == ("POST", "https://svc/api/v1/instances/handoff/redeem")
    assert json.loads(request.data) == {"code": "k3x9"}
    assert request.get_header("Authorization") == "Bearer at-1"
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_cloud.py -q`
Expected: 2 FAIL (`AttributeError`).

- [ ] **Step 3: Implement**

Append to `class Cloud` in `runtime/eggie_api/infra/cloud.py`:

```python
    def instance_certificate(self, token: str):
        """The site's certificate, issued and renewed by the service."""
        return self.call("GET", "/v1/instances/me/certificate", token=token)

    def redeem_instance_handoff(self, token: str, code: str):
        """Whether a sign-in code the web app minted belongs to this
        instance's owner; the service consumes it either way."""
        return self.call("POST", "/v1/instances/handoff/redeem", token=token,
                         body={"code": code})
```

Append to `FakeCloud` in `tests/runtime/api/fake_cloud.py`:

```python
    def instance_certificate(self, token):
        return self._next("instance_certificate", token)

    def redeem_instance_handoff(self, token, code):
        return self._next("redeem_instance_handoff", token, code)
```

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_cloud.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/infra/cloud.py tests/runtime/api/fake_cloud.py tests/runtime/api/test_cloud.py
git commit -m "Cloud client: instance certificate fetch and handoff redemption

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: account seeding from `account.json`

**Files:**
- Modify: `runtime/eggie_api/infra/repos/account.py`, `runtime/eggie_api/services/account.py`, `runtime/eggie_api/__main__.py`
- Test: `tests/runtime/api/test_account.py`

**Interfaces:**
- Produces: `AccountRepo.seed(*, device_id, access_token, refresh_token, access_expires_at)`; `Account.seed_from(path: Path) -> bool` (True when imported; the file is deleted only then; `on_signed_in` fires). `__main__` calls it before `resume()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/api/test_account.py`:

```python
import json


def _seed_file(tmp_path, **fields):
    body = {"device_id": "dev-9", "access_token": "at-s", "refresh_token": "rt-s",
            "expires_in": 3600, **fields}
    path = tmp_path / "account.json"
    path.write_text(json.dumps(body))
    return path


def test_a_seed_file_signs_the_instance_in_as_that_device_and_is_deleted(tmp_path):
    account, clock, _ = make(tmp_path, FakeCloud())
    woken = []
    account.on_signed_in = lambda: woken.append(True)
    path = _seed_file(tmp_path)

    assert account.seed_from(path) is True
    row = account._account.get()
    assert (row["device_id"], row["access_token"], row["refresh_token"]) == ("dev-9", "at-s", "rt-s")
    assert row["access_expires_at"] == clock.now + 3600
    assert account.signed_in and account.device_id == "dev-9"
    assert not path.exists()
    assert woken == [True]


def test_a_signed_in_instance_ignores_a_seed_file_and_leaves_it(tmp_path):
    account, _ = signed_in(tmp_path, FakeCloud())
    path = _seed_file(tmp_path)

    assert account.seed_from(path) is False
    assert account._account.get()["access_token"] == "at-1"
    assert path.exists()


def test_a_malformed_seed_file_changes_nothing_and_stays(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud())
    path = tmp_path / "account.json"
    path.write_text('{"device_id": "dev-9", "access_token": ""}')

    assert account.seed_from(path) is False
    assert not account.signed_in
    assert path.exists()


def test_no_seed_file_is_the_ordinary_case(tmp_path):
    account, _, _ = make(tmp_path, FakeCloud())
    assert account.seed_from(tmp_path / "absent.json") is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_account.py -q -k seed`
Expected: 4 FAIL (`AttributeError: 'Account' object has no attribute 'seed_from'`).

- [ ] **Step 3: Implement**

`runtime/eggie_api/infra/repos/account.py` — add to `AccountRepo`:

```python
    def seed(self, *, device_id: str, access_token: str, refresh_token: str,
             access_expires_at: float) -> None:
        # The one write allowed to change device_id: the service minted this
        # device before first boot, and nothing has been recorded under the
        # placeholder id yet.
        with self._db.tx() as conn:
            conn.execute("UPDATE account SET device_id=?, access_token=?, refresh_token=?, "
                         "access_expires_at=?, last_error=NULL WHERE id=1",
                         (device_id, access_token, refresh_token, access_expires_at))
```

`runtime/eggie_api/services/account.py` — add `import json` and `from pathlib import Path` to the imports, and the method after `status`:

```python
    def seed_from(self, path: Path) -> bool:
        """A token pair the service left for this machine before first boot.
        Imported once, only while nothing is signed in; the file is deleted
        only on import, so a stray one stays visible to whoever looks."""
        try:
            raw = json.loads(path.read_text())
            device_id, access, refresh = raw["device_id"], raw["access_token"], raw["refresh_token"]
            expires_in = float(raw["expires_in"])
            if not all(isinstance(v, str) and v for v in (device_id, access, refresh)):
                raise ValueError("a token field is empty")
        except FileNotFoundError:
            return False
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("ignoring %s: %s", path, e)
            return False
        with self._write_lock:
            if self._account.get()["access_token"]:
                log.warning("ignoring %s: this machine is already signed in", path)
                return False
            self._account.seed(device_id=device_id, access_token=access,
                               refresh_token=refresh,
                               access_expires_at=self._clock() + expires_in)
        path.unlink(missing_ok=True)
        self.on_signed_in()
        return True
```

`runtime/eggie_api/__main__.py` — in `main()`, before `services.account.resume()`:

```python
    services.account.seed_from(config.account_seed)
```

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_account.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/infra/repos/account.py runtime/eggie_api/services/account.py runtime/eggie_api/__main__.py tests/runtime/api/test_account.py
git commit -m "Account: import the service-seeded token pair on first start

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: handoff redemption through the service

**Files:**
- Create: `runtime/eggie_api/services/site.py`
- Modify: `runtime/eggie_api/services/sessions.py` (add `start()`), `runtime/eggie_api/rest/routers/sessions.py`, `runtime/eggie_api/wiring.py`
- Test: `tests/runtime/api/test_sessions.py`

**Interfaces:**
- Consumes: `Cloud.redeem_instance_handoff` (Task 8), `Account.authed`, `Sessions`.
- Produces: `Sessions.start() -> str` (a session with no code); `SiteSignIn(sessions, account, cloud, *, remote: bool).redeem(code) -> str | None`; `Services.site: SiteSignIn`; `POST /api/session` calls `s.site.redeem`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/api/test_sessions.py`:

```python
import dataclasses

from eggie_api.services.account import Account
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from tests.runtime.api.conftest import FakeRunner
from tests.runtime.api.fake_cloud import FakeCloud


def _site_app(env, cloud, *, site=True):
    repos = Repos.open(Database(env.config.state_db.with_name("site.db")))
    repos.account.update(access_token="at", refresh_token="rt",
                         access_expires_at=10**12, org_id="org-1")
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    config = dataclasses.replace(env.config, site_host="foo.eggie.io",
                                 domain="foo.eggie.space") if site else env.config
    app = create_app(config=config, runner=FakeRunner(), repos=repos,
                     account=account, cloud=cloud)
    headers = {**SITE_BROWSER, **SITE_ORIGIN} if site else {**BROWSER, **ORIGIN}
    return app, TestClient(app, headers=headers), TestClient(app, headers=AUTH)


def test_a_code_the_service_vouches_for_signs_the_browser_in_on_a_site(env):
    cloud = FakeCloud(redeem_instance_handoff=[{"ok": True}])
    _, browser, _ = _site_app(env, cloud)
    assert browser.post("/api/session", json={"code": "from-web-app"}).status_code == 200
    assert browser.get("/api/projects").status_code == 200
    assert cloud.calls == [("redeem_instance_handoff", "at", "from-web-app")]


def test_a_code_the_service_refuses_is_handoff_invalid(env):
    cloud = FakeCloud(redeem_instance_handoff=[CloudError("handoff_invalid", "no", 404)])
    _, browser, _ = _site_app(env, cloud)
    resp = browser.post("/api/session", json={"code": "stale"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (401, "handoff_invalid")


def test_an_unreachable_service_is_handoff_invalid_too(env):
    _, browser, _ = _site_app(env, FakeCloud(redeem_instance_handoff=[CloudUnavailable("down")]))
    assert browser.post("/api/session", json={"code": "x"}).status_code == 401


def test_a_locally_minted_code_never_goes_to_the_service_on_a_site(env):
    cloud = FakeCloud()
    _, browser, bearer = _site_app(env, cloud)
    code = bearer.post("/sessions/handoff").json()["code"]
    assert browser.post("/api/session", json={"code": code}).status_code == 200
    assert cloud.calls == []


def test_an_unknown_code_is_not_sent_to_the_service_outside_a_site(env):
    cloud = FakeCloud()
    _, browser, _ = _site_app(env, cloud, site=False)
    assert browser.post("/api/session", json={"code": "x"}).status_code == 401
    assert cloud.calls == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_sessions.py -q`
Expected: the first test FAILS with 401; the "outside a site" test passes already (it pins behavior).

- [ ] **Step 3: Implement**

`runtime/eggie_api/services/sessions.py` — refactor `redeem` and add `start`:

```python
    def redeem(self, code: str) -> str | None:
        with self._lock:
            expires = self._codes.pop(code, None)
        if expires is None or expires <= self._clock():
            return None
        return self.start()

    def start(self) -> str:
        session_id = secrets.token_urlsafe(32)
        self._sessions.add(_hash(session_id), self._clock() + SESSION_TTL)
        return session_id
```

Create `runtime/eggie_api/services/site.py`:

```python
from __future__ import annotations

import logging

from ..infra.cloud import CloudError, CloudUnavailable
from .account import NotSignedIn
from .sessions import Sessions

log = logging.getLogger("eggie.site")


class SiteSignIn:
    """Trades a handoff code for a browser session. Codes minted here (the
    desktop, a signed-in page) are checked locally. On a site, a code the
    web app minted is checked with the service, which knows this instance's
    owner; the instance never sees who that is."""

    def __init__(self, sessions: Sessions, account, cloud, *, remote: bool):
        self._sessions = sessions
        self._account = account
        self._cloud = cloud
        self._remote = remote

    def redeem(self, code: str) -> str | None:
        session_id = self._sessions.redeem(code)
        if session_id is not None or not self._remote:
            return session_id
        try:
            self._account.authed(
                lambda token: self._cloud.redeem_instance_handoff(token, code))
        except NotSignedIn:
            log.warning("a sign-in link arrived but this server is not signed in")
            return None
        except (CloudError, CloudUnavailable) as e:
            log.info("the service did not accept a sign-in link: %s", e)
            return None
        return self._sessions.start()
```

`runtime/eggie_api/rest/routers/sessions.py` — in `start_session`:

```python
        session_id = s.site.redeem(body.code)
        if session_id is None:
            raise Unauthorized("handoff_invalid", "that sign-in link has already "
                            "been used or has run out; open Eggie again from "
                            "where you started")
```

`runtime/eggie_api/wiring.py` — import `from .services.site import SiteSignIn`, add the field `site: SiteSignIn` to `Services`, and in `build` after `sessions = …` and `account = …`:

```python
    site = SiteSignIn(sessions, account, cloud, remote=config.site_host is not None)
```

and pass `site=site` to `Services(...)`.

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/services/site.py runtime/eggie_api/services/sessions.py runtime/eggie_api/rest/routers/sessions.py runtime/eggie_api/wiring.py tests/runtime/api/test_sessions.py
git commit -m "Sessions: redeem a web-app handoff code with the service on a site

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: the certificate service

**Files:**
- Create: `runtime/eggie_api/services/certificate.py`
- Modify: `runtime/eggie_api/wiring.py`
- Test: `tests/runtime/api/test_certificate.py` (new)

**Interfaces:**
- Consumes: `Cloud.instance_certificate` (Task 8), `Account.authed` / `Account.signed_in`.
- Produces: `Certificate(directory, account, cloud, *, names, clock=time.time)` with `refresh()` (a sync pass), `cert_path`, `key_path`, `installed_not_after()`. Files: `site.crt` (0644), `site.key` (0600), `site.not_after` (epoch seconds). `Services.certificate: Certificate | None`, its `refresh` appended to the `SyncLoop` passes in site mode.

- [ ] **Step 1: Write the failing tests**

Create `tests/runtime/api/test_certificate.py`:

```python
import os
import stat

from eggie_api.services.account import Account
from eggie_api.services.certificate import CHECK_EVERY, Certificate
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from tests.runtime.api.fake_cloud import FakeCloud

NAMES = ("foo.eggie.io", "foo.eggie.space", "*.foo.eggie.space")


def issued(not_after="2027-01-11T10:00:00Z", **over):
    return {"names": list(NAMES), "certificate": "CERT-" + not_after,
            "private_key": "KEY-" + not_after, "not_after": not_after, **over}


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def make(tmp_path, cloud, *, signed_in=True):
    repos = Repos.open(Database(tmp_path / "state.db"))
    if signed_in:
        repos.account.update(access_token="at", refresh_token="rt",
                             access_expires_at=10**12, org_id="org-1")
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    clock = Clock()
    cert = Certificate(tmp_path / "tls", account, cloud, names=NAMES, clock=clock)
    return cert, clock


def test_a_missing_certificate_is_fetched_and_written_with_a_private_key(tmp_path):
    cloud = FakeCloud(instance_certificate=[issued()])
    cert, _ = make(tmp_path, cloud)
    cert.refresh()
    assert cert.cert_path.read_text() == "CERT-2027-01-11T10:00:00Z"
    assert cert.key_path.read_text() == "KEY-2027-01-11T10:00:00Z"
    assert stat.S_IMODE(os.stat(cert.key_path).st_mode) == 0o600
    assert cert.installed_not_after() == 1799661600.0
    assert not list(cert.cert_path.parent.glob("*.tmp"))


def test_an_installed_certificate_is_rechecked_once_a_day(tmp_path):
    cloud = FakeCloud(instance_certificate=[issued(), issued()])
    cert, clock = make(tmp_path, cloud)
    cert.refresh()
    clock.now += CHECK_EVERY - 1
    cert.refresh()
    assert len(cloud.calls) == 1
    clock.now += 2
    cert.refresh()
    assert len(cloud.calls) == 2


def test_the_same_expiry_leaves_the_files_alone_a_later_one_replaces_them(tmp_path):
    cloud = FakeCloud(instance_certificate=[
        issued(), issued(certificate="CERT-rewritten"), issued("2027-03-01T00:00:00Z")])
    cert, clock = make(tmp_path, cloud)
    cert.refresh()
    clock.now += CHECK_EVERY + 1
    cert.refresh()
    assert cert.cert_path.read_text() == "CERT-2027-01-11T10:00:00Z"
    clock.now += CHECK_EVERY + 1
    cert.refresh()
    assert cert.cert_path.read_text() == "CERT-2027-03-01T00:00:00Z"
    assert cert.key_path.read_text() == "KEY-2027-03-01T00:00:00Z"


def test_a_failed_fetch_is_retried_on_the_next_pass(tmp_path):
    # First boot often runs before the network is up; a day's wait would
    # leave Traefik on its self-signed certificate until tomorrow.
    cloud = FakeCloud(instance_certificate=[CloudUnavailable("down"), issued()])
    cert, _ = make(tmp_path, cloud)
    cert.refresh()
    assert not cert.cert_path.exists()
    cert.refresh()
    assert cert.cert_path.exists()


def test_a_failed_fetch_keeps_the_installed_files(tmp_path):
    cloud = FakeCloud(instance_certificate=[issued(), CloudError("no_certificate", "n", 404)])
    cert, clock = make(tmp_path, cloud)
    cert.refresh()
    clock.now += CHECK_EVERY + 1
    cert.refresh()
    assert cert.cert_path.read_text() == "CERT-2027-01-11T10:00:00Z"


def test_a_certificate_that_misses_a_name_is_refused(tmp_path):
    cloud = FakeCloud(instance_certificate=[issued(names=["foo.eggie.io", "foo.eggie.space"])])
    cert, _ = make(tmp_path, cloud)
    cert.refresh()
    assert not cert.cert_path.exists() and not cert.key_path.exists()


def test_nothing_is_fetched_while_not_signed_in(tmp_path):
    cloud = FakeCloud()
    cert, _ = make(tmp_path, cloud, signed_in=False)
    cert.refresh()
    assert cloud.calls == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_certificate.py -q`
Expected: ImportError (`eggie_api.services.certificate`).

- [ ] **Step 3: Implement**

Create `runtime/eggie_api/services/certificate.py`:

```python
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ..infra.cloud import CloudError, CloudUnavailable
from .account import NotSignedIn

log = logging.getLogger("eggie.certificate")
CHECK_EVERY = 24 * 3600


def _epoch(value: str) -> float:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _write(path: Path, text: str, mode: int) -> None:
    # Temp name then rename: Traefik watches the directory and must never
    # read a half-written file.
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


class Certificate:
    """The site's TLS certificate. The service issues and renews it; this
    keeps the files Traefik reads current, as one pass of the sync loop."""

    def __init__(self, directory: Path, account, cloud, *, names: tuple[str, ...],
                 clock=time.time):
        self._dir = Path(directory)
        self._account = account
        self._cloud = cloud
        self._names = set(names)
        self._clock = clock
        self._checked_at: float | None = None

    @property
    def cert_path(self) -> Path:
        return self._dir / "site.crt"

    @property
    def key_path(self) -> Path:
        return self._dir / "site.key"

    @property
    def _expiry_path(self) -> Path:
        return self._dir / "site.not_after"

    def installed_not_after(self) -> float | None:
        try:
            return float(self._expiry_path.read_text())
        except (OSError, ValueError):
            return None

    def refresh(self) -> None:
        """Fetch when nothing is installed, else once a day. A failure is
        retried on the next pass: the daily spacing starts only after an
        answer from the service."""
        if not self._account.signed_in:
            return
        now = self._clock()
        have = self.cert_path.exists() and self.key_path.exists()
        if have and self._checked_at is not None and now - self._checked_at < CHECK_EVERY:
            return
        try:
            out = self._account.authed(self._cloud.instance_certificate)
        except NotSignedIn:
            return
        except (CloudError, CloudUnavailable) as e:
            log.warning("could not fetch the site certificate: %s", e)
            return
        self._checked_at = now
        try:
            self._install(out, have)
        except (KeyError, TypeError, ValueError, OSError) as e:
            log.warning("the service's certificate was not installed: %s", e)

    def _install(self, out: dict, have: bool) -> None:
        missing = self._names - set(out["names"])
        if missing:
            raise ValueError(f"it does not cover {sorted(missing)}")
        not_after = _epoch(out["not_after"])
        current = self.installed_not_after()
        if have and current is not None and not_after <= current:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        _write(self.key_path, out["private_key"], 0o600)
        _write(self.cert_path, out["certificate"], 0o644)
        _write(self._expiry_path, repr(not_after), 0o644)
        log.info("site certificate installed, valid until %s", out["not_after"])
```

`runtime/eggie_api/wiring.py` — import `from .services.certificate import Certificate`, add `certificate: Certificate | None` to `Services`, and replace the `sync = SyncLoop(...)` line with:

```python
    certificate = None
    if config.site_host:
        certificate = Certificate(
            config.tls_dir, account, cloud,
            names=(config.site_host, config.domain, f"*.{config.domain}"))
    passes = [public.reconcile, lambda: run_pass(account, cloud, repos)]
    if certificate is not None:
        passes.append(certificate.refresh)
    sync = SyncLoop(passes)
```

and pass `certificate=certificate` to `Services(...)`.

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api -q`
Expected: all PASS (`test_layering.py` must stay green: `services` imports only `infra`).

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/services/certificate.py runtime/eggie_api/wiring.py tests/runtime/api/test_certificate.py
git commit -m "Certificate service: fetch the site certificate from the service and keep it current

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: surface — `health.site`, site SSH facts, public URL refused

**Files:**
- Modify: `runtime/eggie_api/services/system.py`, `runtime/eggie_api/infra/connect.py`, `runtime/eggie_api/services/public.py`, `runtime/eggie_api/wiring.py`
- Test: `tests/runtime/api/test_site.py`, `tests/runtime/api/test_connect.py`, `tests/runtime/api/test_public.py`

**Interfaces:**
- Produces: `/health` and `/api/health` carry `"site": <site_host or null>`; `connect.facts(path, site_host=None)` knows `"vm": "site"`; `Public(..., site: bool = False)` reports `unavailable` with reason `not_available` and refuses `enable` with 409 `not_available` on a site.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/api/test_site.py`:

```python
def test_health_has_no_site_locally(env):
    assert env.client.get("/health").json()["site"] is None


def test_health_names_the_site(site_env):
    assert site_env.client.get("/health").json()["site"] == "foo.eggie.io"
```

Append to `tests/runtime/api/test_connect.py`:

```python
def test_a_site_tells_agents_its_host_on_the_standard_ssh_port_with_no_key_file(tmp_path):
    facts = connect.facts(_write(tmp_path, {"vm": "site", "user": "ada"}), site_host="foo.eggie.io")
    assert facts == {"vm": "site", "ssh": {"host": "foo.eggie.io", "port": 22,
                                           "user": "ada", "key_file": None}}


def test_a_site_record_without_a_site_host_has_no_ssh_facts(tmp_path):
    assert connect.facts(_write(tmp_path, {"vm": "site", "user": "ada"})) == {"vm": "site", "ssh": None}
```

In `tests/runtime/api/test_public.py`, add `site=False` to `make`'s keyword parameters and pass `site=site` into its `Public(...)` call (`make` returns `public, repos, runner, clock`). Then append:

```python
def test_on_a_site_public_urls_are_unavailable_because_every_project_already_has_one(tmp_path):
    public, _, _, _ = make(tmp_path, FakeCloud(), site=True)
    assert public.status("blog") == {"state": "unavailable",
                                     "reason": {"code": "not_available",
                                                "message": MESSAGES["not_available"]}}
    with pytest.raises(PublicUnavailable) as caught:
        public.enable("blog")
    assert caught.value.code == "not_available"
```

- [ ] **Step 2: Run to verify they fail**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime/api/test_site.py tests/runtime/api/test_connect.py tests/runtime/api/test_public.py -q`
Expected: the new tests FAIL (`KeyError: 'site'`, `TypeError: facts() got an unexpected keyword`, `TypeError: Public.__init__() got an unexpected keyword 'site'`).

- [ ] **Step 3: Implement**

`services/system.py` — in `health()` add `"site": self._config.site_host,` after `"api"`, and:

```python
    def connect(self) -> dict:
        return connect.facts(Path(self._config.connect_path),
                             site_host=self._config.site_host)
```

`infra/connect.py`:

```python
_KINDS = {"wsl", "lima", "site"}


def facts(path: Path, site_host: str | None = None) -> dict:
    """How a coding agent reaches the VM, from what install.sh recorded.
    Anything unreadable reads as an unknown VM: the console then chooses a
    guide from the browser instead."""
    try:
        recorded = json.loads(path.read_text())
    except (OSError, ValueError):
        recorded = {}
    if not isinstance(recorded, dict):
        recorded = {}
    vm = recorded.get("vm") if recorded.get("vm") in _KINDS else "other"
    user = recorded.get("user") if isinstance(recorded.get("user"), str) else ""
    ssh = None
    if vm == "lima" and user:
        ssh = {"host": "127.0.0.1", "port": constants.LIMA_SSH_PORT,
               "user": user, "key_file": constants.LIMA_KEY_FILE}
    elif vm == "site" and user and site_host:
        # The service installed the user's own key; there is no file to name.
        ssh = {"host": site_host, "port": 22, "user": user, "key_file": None}
    return {"vm": vm, "ssh": ssh}
```

`services/public.py` — add to `MESSAGES`:

```python
    "not_available": "Every project on this server already has a public address.",
```

constructor: add `site: bool = False` after `hosts_for` and store `self._site = site`; `_blocked`:

```python
    def _blocked(self, local_id: str) -> str | None:
        if self._site:
            return "not_available"
        if not self._account.signed_in:
            return "signed_out"
        …
```

`wiring.py` — pass `site=config.site_host is not None` to `Public(...)`.

- [ ] **Step 4: Run to verify they pass**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest tests/runtime -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/eggie_api/services/system.py runtime/eggie_api/infra/connect.py runtime/eggie_api/services/public.py runtime/eggie_api/wiring.py tests/runtime/api
git commit -m "API surface on a site: health.site, site SSH facts, public URLs not available

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: console learns the site

**Files:**
- Create: `runtime/web/apps/console/src/site/site.ts`, `runtime/web/apps/console/src/site/SiteContext.tsx`, `runtime/web/apps/console/src/site/site.test.ts`
- Modify: `runtime/web/apps/console/src/App.tsx`, `runtime/web/apps/console/src/screens/SignedOut.tsx`

**Interfaces:**
- Produces: `fetchSite(fetchImpl): Promise<string | null>`; `SiteContext` (`createContext<string | null>(null)`) and `useSite()`; `App` provides it from one `GET /api/health` at mount.

- [ ] **Step 1: Write the failing test**

`runtime/web/apps/console/src/site/site.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { fetchSite } from "./site";

function answering(status: number, body: unknown): typeof fetch {
  return (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;
}

describe("fetchSite", () => {
  it("is the hostname the API reports on a site", async () => {
    expect(await fetchSite(answering(200, { api: 1, site: "foo.eggie.io" }))).toBe("foo.eggie.io");
  });

  it("is null locally, on an older API without the field, and on an empty name", async () => {
    expect(await fetchSite(answering(200, { api: 1, site: null }))).toBeNull();
    expect(await fetchSite(answering(200, { api: 1 }))).toBeNull();
    expect(await fetchSite(answering(200, { api: 1, site: "" }))).toBeNull();
  });

  it("is null when the API does not answer or refuses the host", async () => {
    expect(await fetchSite(answering(403, { error: { code: "forbidden_host" } }))).toBeNull();
    const refused = (async () => { throw new TypeError("Failed to fetch"); }) as typeof fetch;
    expect(await fetchSite(refused)).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd runtime/web && npx vitest run apps/console/src/site/site.test.ts`
Expected: FAIL (cannot resolve `./site`).

- [ ] **Step 3: Implement**

`site/site.ts`:

```ts
// Whether this console runs on a site (a server with its own hostnames), by
// the name the API reports. Null means the usual VM on the user's computer.
export async function fetchSite(fetchImpl: typeof fetch): Promise<string | null> {
  try {
    const response = await fetchImpl("/api/health");
    if (!response.ok) return null;
    const body = (await response.json()) as { site?: unknown } | null;
    const site = body?.site;
    return typeof site === "string" && site !== "" ? site : null;
  } catch {
    return null;
  }
}
```

`site/SiteContext.tsx`:

```tsx
import { createContext, useContext } from "react";

export const SiteContext = createContext<string | null>(null);

export const useSite = () => useContext(SiteContext);
```

`App.tsx` — add the imports `import { fetchSite } from "./site/site";` and `import { SiteContext } from "./site/SiteContext";`, and change `App`:

```tsx
export function App({ handoff, home = null }: { handoff: string | null; home?: string | null }) {
  const [site, setSite] = useState<string | null>(null);
  useEffect(() => {
    void fetchSite((input, init) => fetch(input, init)).then(setSite);
  }, []);
  return (
    <DesktopHome.Provider value={home}>
      <SiteContext.Provider value={site}>
        <Screens handoff={handoff} />
      </SiteContext.Provider>
    </DesktopHome.Provider>
  );
}
```

`screens/SignedOut.tsx` — import `useSite` and replace the second paragraph:

```tsx
      <p className={s.lead}>
        {site ? (
          <>Go back to the Eggie web app and open this console from there again.</>
        ) : (
          <>Open the Eggie app on your desktop and press <strong>Open Eggie</strong>. It'll hand the keys back.</>
        )}
      </p>
```

with `const site = useSite();` at the top of the component.

- [ ] **Step 4: Run tests and typecheck**

Run: `cd runtime/web && npx vitest run apps/console/src/site/site.test.ts && npm run typecheck`
Expected: PASS, typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add runtime/web/apps/console/src/site runtime/web/apps/console/src/App.tsx runtime/web/apps/console/src/screens/SignedOut.tsx
git commit -m "Console: learn the site from /api/health; signed-out copy points at the web app

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: console controls that do not apply on a site

**Files:**
- Modify: `runtime/web/apps/console/src/screens/project/Tiles.tsx`, `screens/project/ProjectPage.tsx`, `shell/AccountMenu.tsx`, `agents/catalog.ts`, `agents/card.ts`, `mocks/handlers.ts`
- Test: `runtime/web/apps/console/src/agents/card.test.ts`

**Interfaces:**
- Consumes: `useSite()` (Task 13).
- Produces: `Tiles` prop `showPublic: boolean`; `Connect.vm` includes `"site"` and `ssh.key_file` is `string | null`; `cardRows` drops a row whose template names a null fact other than `user`; mock scenario `site`.

- [ ] **Step 1: Write the failing test**

Append to `agents/card.test.ts`:

```ts
const site = { vm: "site" as const, ssh: { host: "foo.eggie.io", port: 22, user: "ada", key_file: null } };

it("drops the key-file row on a site, where the user's own key is installed", () => {
  expect(cardRows(fourFields, site)).toEqual([
    { label: "Host", value: "foo.eggie.io", copy: true },
    { label: "Port", value: "22", copy: true },
    { label: "User", value: "ada", copy: true },
  ]);
  expect(cardRows(userAtHost, site)?.[0]).toEqual({ label: "SSH Host", value: "ada@foo.eggie.io", copy: true });
});
```

(inside the existing `describe("cardRows", …)` block).

- [ ] **Step 2: Run to verify it fails**

Run: `cd runtime/web && npx vitest run apps/console/src/agents/card.test.ts`
Expected: typecheck/assert FAIL (`key_file: null` not assignable; four rows returned).

- [ ] **Step 3: Implement**

`agents/catalog.ts`:

```ts
export interface Connect {
  vm: "wsl" | "lima" | "site" | "other";
  ssh: { host: string; port: number; user: string; key_file: string | null } | null;
}
```

`agents/card.ts` — replace the `return guide.card.map(...)` body:

```ts
  const rows: CardRow[] = [];
  for (const { label, value } of guide.card) {
    let known = true;
    let applies = true;
    const filled = value.replace(/\{([^}]*)\}/g, (_, name: Fact) => {
      const fact = facts[name];
      if (fact !== null) return fact;
      // Only the user may be unknown yet still worth a row; a fact that does
      // not exist here (a site has no key file) drops the whole row.
      if (name === "user") {
        known = false;
        return UNKNOWN_USER;
      }
      applies = false;
      return "";
    });
    // Copying "your Mac user name@127.0.0.1" would paste a broken address.
    if (applies) rows.push({ label, value: filled, copy: known });
  }
  return rows;
```

`screens/project/Tiles.tsx` — add `showPublic: boolean` to the props and wrap the public button:

```tsx
      {showPublic && (
        <button type="button" className={cx(s.tile, publicDisabled && s.off)} disabled={publicDisabled} onClick={onPublic}>
          {GLOBE}Public address<small>{publicLine}</small>
        </button>
      )}
```

`screens/project/ProjectPage.tsx` — `const site = useSite();` (import from `../../site/SiteContext`) and pass `showPublic={site === null}` to `<Tiles …>`.

`shell/AccountMenu.tsx` — `const site = useSite();` and wrap the sign-out button: `{site === null && (<button …>Sign out</button>)}`.

`mocks/handlers.ts` — add `"site"` to `SCENARIOS`; in the `/api/health` handler add `site: scenario === "site" ? "foo.eggie.io" : null,`; in `/api/connect`: `if (scenario === "site") return HttpResponse.json({ vm: "site", ssh: { host: "foo.eggie.io", port: 22, user: "ada", key_file: null } });`.

- [ ] **Step 4: Run tests, typecheck, build**

Run: `cd runtime/web && npm test && npm run typecheck && npm run build`
Expected: all PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add runtime/web/apps/console/src
git commit -m "Console on a site: no public toggle, no sign-out, SSH card without a key file

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: documentation

**Files:**
- Modify: `docs/security.md`, `docs/vm.md`, `docs/architecture.md`, `docs/release-testing.md`, `runtime/CLAUDE.md`, `runtime/install/CLAUDE.md`, `runtime/eggie_api/CLAUDE.md`, `runtime/web/CLAUDE.md`

- [ ] **Step 1: `docs/security.md`**

Add rows to the "Who can reach the VM from outside" table:

```markdown
| Site, port `443` | Traefik: the console on the site host, the API at `/api`, every project under the site domain | The internet (site mode only) |
| Site, port `80` | A redirect to 443 | The internet (site mode only) |
| Site, port `22` | The login account | Whoever holds the SSH key the service installed (site mode only) |
```

Add a section before "Before adding a way in":

```markdown
## Site mode (a server the service provisions)

On a site the boundary is the server's firewall, which the service manages: 22, 80 and 443
only. Inside it, everything above still holds — every login account, project and container is
root-equivalent — plus:

- The console and `/api` are on the internet. A session comes only from a handoff code the
  service minted for this instance's owner and the instance redeemed with the service
  (`services/site.py`); the cookie is `Secure`. The console and the projects sit on different
  registered domains (`eggie.io` / `eggie.space`), so a project cannot set a cookie the console's
  origin would read.
- Projects are public with whatever auth they have themselves, like public URLs locally.
- The bearer API binds to loopback (`EGGIE_API_BIND`). Project `ports:` are still published on
  all interfaces; only the firewall blocks them.
- The certificate key is `0600`, written by the API, mounted read-only into Traefik; it covers
  this instance's three names only. The seeded token file is deleted on import.
- A snapshot made with `snapshot-prep.sh` holds no token, database or key.
```

- [ ] **Step 2: `docs/vm.md`**

Add after the "Ports" paragraph:

```markdown
## Site mode (a server)

The same runtime on a server the Eggie service provisions. `get.sh` with `EGGIE_SITE_HOST` (the
console, e.g. `foo.eggie.io`) and `EGGIE_SITE_DOMAIN` (projects, e.g. `foo.eggie.space`) writes
`/opt/eggie/site.env`; every later run reads it. Differences from a local VM:

| | Local VM | Site |
|---|---|---|
| Edge | `http://localhost:39080` | `https://<site host>` (80 redirects) |
| Project URL | `http://<id>.local.eggie.space:39080` | `https://<id>.<site domain>` |
| Compose | `stack.yml` | `stack.yml` + `runtime/stack.site.yml` |
| Certificate | none | `/opt/eggie/tls/site.{crt,key}`, fetched from the service daily |
| Sign-in | desktop handoff | handoff from the web app, redeemed with the service |
| Account | device-code sign-in | seeded from `/opt/eggie/account.json` at first boot |
| Public URLs | optional | not available (everything already is) |

Build a snapshot: install with no site variables, run `bash /opt/eggie/runtime/install/snapshot-prep.sh`,
image. First boot of a droplet from it: cloud-init writes `account.json`, creates the login user,
and runs `get.sh` with `EGGIE_RUNTIME_REPAIR=1` plus the two site names (spec §3.2).
```

- [ ] **Step 3: `docs/architecture.md`**

After "How a project runs", add:

```markdown
### Site mode

The runtime also runs on a server the Eggie service provisions. Nothing in the host changes: the
service creates the droplet from a snapshot, its DNS rows and its certificate, seeds the account
token through cloud-init, and runs `get.sh` with `EGGIE_SITE_HOST`/`EGGIE_SITE_DOMAIN`. Inside the
VM, `stack.site.yml` puts Traefik on 443 with the certificate from `/opt/eggie/tls`
(`services/certificate.py` keeps it current), `rest/auth.py` admits the site host, and
`services/site.py` redeems web-app handoff codes with the service. Design:
`docs/superpowers/specs/2026-10-13-remote-instance-design.md`.
```

and rows in the file table: `runtime/stack.site.yml`, `runtime/install/snapshot-prep.sh`, `services/site.py`, `services/certificate.py` (one line each, in the table's style).

- [ ] **Step 4: `docs/release-testing.md`**

Append:

```markdown
## Site mode (a droplet from the snapshot)

| # | Action | Pass when | Result |
|---|---|---|---|
| S1 | Build the snapshot (install, `snapshot-prep.sh`, image) and provision a droplet from it with the service | `https://<site host>` answers within two minutes of boot with a valid certificate; `/opt/eggie/account.json` is gone | |
| S2 | "Open console" from the web app | The console opens signed in; the account menu has no sign-out; projects page has no public-address tile | |
| S3 | Add a project and start it | Its URL is `https://<id>.<site domain>` and answers with the same certificate | |
| S4 | SSH in as the login account; `eggie status` | The CLI works over loopback; the agents card in the console shows the site host, port 22, no key file | |
| S5 | Reboot the droplet | The stack comes back on 443; the boot update moves `runtime.version` if a newer release exists; projects resume | |
| S6 | Renew the certificate on the service side; wait a day or restart the api container | `site.not_after` moves; Traefik serves the new certificate without a restart | |
| S7 | `curl http://<ip>:39099/health` and `:39080` from outside | Both refused by the firewall; `curl 127.0.0.1:39099/health` inside works | |
```

- [ ] **Step 5: CLAUDE.md files**

`runtime/CLAUDE.md` — in the `stack.yml` bullet add: "`stack.site.yml` is the override for a site (Traefik on 443 with TLS from `/opt/eggie/tls`); `install.sh` adds it to every stack command when `/opt/eggie/site.env` exists."

`runtime/install/CLAUDE.md` — add a section:

```markdown
## Site mode

`get.sh` writes `/opt/eggie/site.env` from `EGGIE_SITE_HOST`/`EGGIE_SITE_DOMAIN` (both or neither,
bare lowercase hostnames); a run without them keeps the file. `install.sh` sources it: appends
`EGGIE_SITE_HOST`, `EGGIE_DOMAIN` and `EGGIE_API_BIND=127.0.0.1` to `.env`, adds
`-f $RUNTIME_DIR/stack.site.yml` (`$SITE_STACK`, unquoted on purpose) to the stack's compose
commands, installs `site/tls.yml` into `/opt/eggie/tls` (`2770 root:docker`: the API writes, Traefik
reads) and records `"vm": "site"`. `snapshot-prep.sh` strips per-instance state before a server is
imaged (`tests/runtime/test_snapshot_prep.py`).
```

`runtime/eggie_api/CLAUDE.md` — add to Feature areas:

```markdown
- **Site** (`services/site.py`, `services/certificate.py`) — `ApiConfig.site_host` (from
  `EGGIE_SITE_HOST`) switches it on. `SiteSignIn.redeem` tries local handoff codes first, then
  `POST /v1/instances/handoff/redeem` with the account token. `Certificate.refresh` is a sync pass:
  fetch when `/opt/eggie/tls/site.crt` is missing, else daily; replace only on a later `not_after`;
  temp-file-and-rename. `Account.seed_from` imports `/opt/eggie/account.json` once at start
  (`__main__`). `/health` carries `site`; `connect.facts` knows `"site"`; `Public` reports
  `not_available`. Browser allowlist and `Secure` cookie: `rest/auth.py::cookie_options`.
```

`runtime/web/CLAUDE.md` — add a bullet under Structure: "`site/site.ts` reads `site` from `/api/health` once at mount into `SiteContext` (`useSite()`); on a site the signed-out screen points at the web app, the public-address tile and sign-out are hidden, and the agents card drops rows for facts that do not exist (`key_file: null`). Mock: `?scenario=site`."

- [ ] **Step 6: Run the whole suite and commit**

Run: `TMPDIR=/home/ihor/tmp python3 -m pytest -q && (cd runtime/web && npm test)`
Expected: all PASS.

```bash
git add docs runtime/CLAUDE.md runtime/install/CLAUDE.md runtime/eggie_api/CLAUDE.md runtime/web/CLAUDE.md
git commit -m "Docs: site mode in security, vm, architecture, release testing and the layer guides

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
