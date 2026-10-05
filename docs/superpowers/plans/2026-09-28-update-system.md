# Update System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The runtime updates itself at VM boot to the newest release the host supports, a host that finds an incompatible runtime updates it at once, and the desktop app updates on one click.

**Architecture:** Each runtime tag declares its API number in `runtime/release.json`. `get.sh` (the one updater, always fetched from `main`) learns to pick the newest tag speaking an accepted API and gains a staged, rolled-back update mode. A boot-time systemd unit runs it with the API list the host last wrote to `/opt/eggie/host.json`. The host writes that file, triggers the same update when `/health` answers an unsupported API, and separately checks `host-v*` GitHub releases to download, verify and launch a newer installer.

**Tech Stack:** bash (get.sh, install.sh, systemd), Python 3.12 stdlib (host), pywebview UI (plain JS/HTML), React console copy, pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-update-system-design.md`

## Global Constraints

- `get.sh` on `main` stays backward compatible: `EGGIE_RUNTIME_REPO`, `EGGIE_RUNTIME_REF`, `EGGIE_RUNTIME_REPAIR`, the marker `/opt/eggie/runtime.version` and exit semantics keep their meaning. New inputs (`EGGIE_RUNTIME_API`, `EGGIE_RUNTIME_UPDATE`) are optional; unset means today's behaviour.
- `resolve_ref` precedence: explicit `EGGIE_RUNTIME_REF` → installed ref on repair → newest `runtime-vN.N.N` tag whose `release.json` `api` is accepted (newest tag when `EGGIE_RUNTIME_API` unset). Tags without `release.json` are skipped when filtering.
- Update mode never leaves a working VM worse: same ref → exit 0, no change; staging failure → installed runtime untouched; `install.sh` failure → previous runtime restored and reinstalled.
- `host/` never imports `eggie_api`; no `sys.platform` / `platform.system()` / `os.name` outside `host/providers/`.
- The host gains no dependency: HTTP through stdlib `urllib` only.
- Host releases: tag `host-vX.Y.Z`, not pre-release; assets `EggieSetup-X.Y.Z.exe`, `EggieSetup-X.Y.Z-arm64.pkg`, `EggieSetup-X.Y.Z-x86_64.pkg`, `SHA256SUMS`.
- Releases API: `https://api.github.com/repos/eggie-io/eggie/releases`.
- Guest paths: `/opt/eggie/host.json` (`{"supported_api": [1]}`), `/opt/eggie/runtime.env`, `/opt/eggie/update.lock`, `/opt/eggie/runtime.prev`, `/opt/eggie/stack.next.yml`.
- Tests: no network, no real VM; shell scripts run against fakes on `PATH`; repo files reached via `Path(__file__).resolve()`. `TMPDIR=<writable dir>` prefix in this sandbox.
- Comments: only for non-obvious edge cases; no ticket or doc references.

## Review Focus

1. **Boot update enabled with `--now`** — `install.sh` must `enable` the unit but never start it, or every install recursively runs an update. Pinned in Task 4.
2. **A developer's VM on a branch ref** — a VM installed from `EGGIE_RUNTIME_REF=feature/x` must not be silently moved to a tag at boot. Pinned in Task 4 (`boot-update.sh` skips non-tag installed refs).
3. **`host.json` with junk or a missing file** — a malformed file must not produce an `EGGIE_RUNTIME_API` that matches nothing or everything; it falls back to the installed release's API. Pinned in Task 4.
4. **Endless auto-update loop in the desktop** — if an update "succeeds" but the probe still reports an unsupported API, the UI must not restart the job forever. Pinned in Task 7 (auto-start once per session; `connect_step` re-checks and raises).
5. **Semver compared as text** — `0.10.0` must beat `0.9.0` for both runtime tags (`sort -V`) and host releases. Pinned in Task 9.

---

### Task 1: Runtime releases declare their API number

**Files:**
- Create: `runtime/release.json`
- Modify: `tests/test_constants_agree.py` (append a test)

**Interfaces:**
- Produces: `runtime/release.json` = `{"api": <API_VERSION>}` on one line, read by `get.sh` (Task 2) and `boot-update.sh` (Task 4).

- [ ] **Step 1: Write the failing test** — append to `tests/test_constants_agree.py`:

```python
def test_the_release_declares_the_api_number_the_service_speaks():
    # get.sh picks a release by this file before installing it; a release
    # declaring the wrong number installs on a host that cannot drive it.
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    release = json.loads((root / "runtime" / "release.json").read_text())
    assert release == {"api": api_constants.API_VERSION}
```

- [ ] **Step 2: Run it to see it fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/test_constants_agree.py -q -k release_declares`
Expected: FAIL, `FileNotFoundError` for `runtime/release.json`.

- [ ] **Step 3: Create `runtime/release.json`** (single line, no spaces inside the braces are required but keep this exact shape):

```json
{"api": 1}
```

- [ ] **Step 4: Run it to see it pass**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/test_constants_agree.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/release.json tests/test_constants_agree.py
git commit -m "Declare the runtime's API number in release.json"
```

---

### Task 2: `get.sh` picks the newest release speaking an accepted API

**Files:**
- Modify: `runtime/install/get.sh` (`resolve_ref`, new `release_api`)
- Test: `tests/runtime/test_get_sh.py`

**Interfaces:**
- Consumes: `runtime/release.json` shape from Task 1, fetched as `$repo/raw/<tag>/runtime/release.json`.
- Produces: `resolve_ref <repo> <marker>` honours `EGGIE_RUNTIME_API` (comma list of integers, e.g. `1,2`).

- [ ] **Step 1: Write the failing tests** — in `tests/runtime/test_get_sh.py`, extend `_resolve` to accept a fake curl and clear the new variables, then add tests:

```python
def _curl_serving(tmp_path: Path, releases: dict) -> str:
    # releases: tag -> release.json body, or None for a tag without the file.
    lines = []
    for tag, body in releases.items():
        if body is not None:
            (tmp_path / f"{tag}.json").write_text(body)
            lines.append(f'*/raw/{tag}/runtime/release.json) cat "{tmp_path / (tag + ".json")}"; exit 0 ;;')
    return ('for last; do :; done\ncase "$last" in\n' + "\n".join(lines)
            + '\n*) exit 22 ;;\nesac\n')


def _resolve(tmp_path, *, tags=(), git_code=0, installed="", releases=None, **env):
    marker = tmp_path / "runtime.version"
    if installed:
        marker.write_text(installed + "\n")
    fakes = {"git": _git_listing(tmp_path, tags, git_code)}
    if releases is not None:
        fakes["curl"] = _curl_serving(tmp_path, releases)
    environ = _bin(tmp_path, **fakes)
    for name in ("EGGIE_RUNTIME_REF", "EGGIE_RUNTIME_REPAIR",
                 "EGGIE_RUNTIME_API", "EGGIE_RUNTIME_UPDATE"):
        environ.pop(name, None)
    environ.update(env)
    return subprocess.run(
        ["bash", "-c", f'source "{GET}" && resolve_ref "{REPO}" "{marker}"'],
        env=environ, capture_output=True, text=True)


def test_the_newest_release_speaking_an_accepted_api_wins_over_a_newer_one_that_does_not(tmp_path):
    result = _resolve(tmp_path,
                      tags=["runtime-v0.9.0", "runtime-v0.10.0", "runtime-v1.0.0"],
                      releases={"runtime-v1.0.0": '{"api": 2}',
                                "runtime-v0.10.0": '{"api": 1}',
                                "runtime-v0.9.0": '{"api": 1}'},
                      EGGIE_RUNTIME_API="1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "runtime-v0.10.0"


def test_any_listed_api_is_accepted(tmp_path):
    result = _resolve(tmp_path, tags=["runtime-v0.1.0", "runtime-v0.2.0"],
                      releases={"runtime-v0.2.0": '{"api": 2}', "runtime-v0.1.0": '{"api": 1}'},
                      EGGIE_RUNTIME_API="1,2")
    assert result.stdout.strip() == "runtime-v0.2.0"


def test_a_release_without_release_json_is_skipped(tmp_path):
    result = _resolve(tmp_path, tags=["runtime-v0.0.7", "runtime-v0.1.0"],
                      releases={"runtime-v0.1.0": None, "runtime-v0.0.7": '{"api": 1}'},
                      EGGIE_RUNTIME_API="1")
    assert result.stdout.strip() == "runtime-v0.0.7"


def test_no_release_speaking_an_accepted_api_is_a_plain_failure(tmp_path):
    result = _resolve(tmp_path, tags=["runtime-v0.1.0"],
                      releases={"runtime-v0.1.0": '{"api": 1}'},
                      EGGIE_RUNTIME_API="2")
    assert result.returncode != 0
    assert "speaks api 2" in result.stderr


def test_an_explicit_ref_is_installed_without_an_api_check(tmp_path):
    result = _resolve(tmp_path, tags=["runtime-v0.1.0"], releases={},
                      EGGIE_RUNTIME_API="1", EGGIE_RUNTIME_REF="feature/x")
    assert result.stdout.strip() == "feature/x"


def test_a_repair_keeps_the_installed_ref_even_with_an_api_list(tmp_path):
    result = _resolve(tmp_path, tags=["runtime-v0.3.0"], installed="runtime-v0.2.0",
                      releases={"runtime-v0.3.0": '{"api": 1}'},
                      EGGIE_RUNTIME_API="1", EGGIE_RUNTIME_REPAIR="1")
    assert result.stdout.strip() == "runtime-v0.2.0"


def test_an_api_list_that_is_not_numbers_is_refused(tmp_path):
    result = _resolve(tmp_path, tags=["runtime-v0.1.0"],
                      releases={"runtime-v0.1.0": '{"api": 1}'},
                      EGGIE_RUNTIME_API="1;rm")
    assert result.returncode != 0
    assert "EGGIE_RUNTIME_API" in result.stderr
```

Also update the two other places in this file that pop env names (`test_fetching_the_script_runs_the_install_not_just_its_functions`, `test_a_successful_install_exits_zero_and_hands_install_sh_the_ref`, `test_an_archive_without_the_runtime_is_a_plain_failure`) to pop `EGGIE_RUNTIME_API` and `EGGIE_RUNTIME_UPDATE` as well.

- [ ] **Step 2: Run them to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/test_get_sh.py -q`
Expected: the new API tests FAIL (newest tag returned regardless of API); older tests still PASS.

- [ ] **Step 3: Implement** — in `runtime/install/get.sh`, add above `resolve_ref` and replace its tag-picking tail:

```bash
# release_api <repo> <tag>: the api number a release declares; empty when it
# declares none (every release cut before release.json existed).
release_api() {
  curl -fsSL "$1/raw/$2/runtime/release.json" 2>/dev/null \
    | tr -d ' \n' | sed -n 's/.*"api":\([0-9][0-9]*\).*/\1/p'
}

# resolve_ref <repo> <marker>: an explicit ref wins, a repair keeps what is
# installed, anything else takes the highest runtime-v* tag -- the highest one
# speaking an api in EGGIE_RUNTIME_API when that is set.
resolve_ref() {
  local repo=$1 marker=$2 tags candidates tag api
  if [[ -n "${EGGIE_RUNTIME_REF:-}" ]]; then
    echo "$EGGIE_RUNTIME_REF"
    return
  fi
  if [[ "${EGGIE_RUNTIME_REPAIR:-}" == 1 && -s "$marker" ]]; then
    cat "$marker"
    return
  fi
  if [[ -n "${EGGIE_RUNTIME_API:-}" && ! "$EGGIE_RUNTIME_API" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    echo "EGGIE_RUNTIME_API must be a comma-separated list of numbers, not '$EGGIE_RUNTIME_API'" >&2
    return 1
  fi
  if ! tags="$(git ls-remote --tags --refs "$repo" 'runtime-v*')"; then
    echo "could not reach $repo to find the latest Eggie runtime" >&2
    return 1
  fi
  # grep exits 1 when no tag matches; the empty result is handled below.
  candidates="$(sed -n 's#.*refs/tags/##p' <<<"$tags" | grep -E '^runtime-v[0-9]+\.[0-9]+\.[0-9]+$' | sort -rV)" || true
  if [[ -z "$candidates" ]]; then
    echo "$repo has no runtime-v* release to install" >&2
    return 1
  fi
  if [[ -z "${EGGIE_RUNTIME_API:-}" ]]; then
    head -n 1 <<<"$candidates"
    return
  fi
  while read -r tag; do
    api="$(release_api "$repo" "$tag")" || true
    if [[ -n "$api" && ",$EGGIE_RUNTIME_API," == *",$api,"* ]]; then
      echo "$tag"
      return
    fi
  done <<<"$candidates"
  echo "no runtime-v* release in $repo speaks api $EGGIE_RUNTIME_API" >&2
  return 1
}
```

(`head -n 1 <<<"$candidates"` reads a here-string, not a pipe, so it cannot SIGPIPE a producer.)

- [ ] **Step 4: Run the file**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/test_get_sh.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/install/get.sh tests/runtime/test_get_sh.py
git commit -m "get.sh picks the newest release speaking an accepted API"
```

---

### Task 3: `get.sh` update mode — lock, no-op, stage, swap, roll back

**Files:**
- Modify: `runtime/install/get.sh` (`main`)
- Test: `tests/runtime/test_get_sh.py`

**Interfaces:**
- Consumes: `resolve_ref` from Task 2.
- Produces: `EGGIE_RUNTIME_UPDATE=1` mode; `/opt/eggie/runtime.env` written on every successful download (`EGGIE_RUNTIME_URL=…`, `EGGIE_RUNTIME_REPO=…`); every run holds `flock /opt/eggie/update.lock`. `EGGIE_RUNTIME_URL` in the environment is recorded as given; otherwise `$REPO/raw/main/runtime/install/get.sh`.

- [ ] **Step 1: Write the failing tests** — add a harness and tests to `tests/runtime/test_get_sh.py`:

```python
def _archive(tmp_path: Path, ref: str, install_body: str, *, stack="services: {}\n") -> Path:
    import io
    tar_path = tmp_path / f"{ref}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for name, body in {"runtime/install/install.sh": install_body,
                           "runtime/stack.yml": stack}.items():
            data = body.encode()
            info = tarfile.TarInfo(name=f"eggie-{ref}/{name}")
            info.size = len(data)
            tar.addfile(tarinfo=info, fileobj=io.BytesIO(data))
    return tar_path


def _update_run(tmp_path, *, installed="runtime-v0.1.0", latest="runtime-v0.2.0",
                pull_code=0, install_code=0, update=True):
    """Runs get.sh against a fake /opt/eggie with runtime-v0.1.0 installed."""
    root = tmp_path / "opt-eggie"
    (root / "runtime" / "install").mkdir(parents=True)
    (root / "runtime" / "install" / "install.sh").write_text(
        f'#!/usr/bin/env bash\necho "old install.sh $*"\necho "$1" > "{root}/runtime.version"\n')
    (root / "runtime" / "keep-me").write_text("old runtime")
    for kept in ("api.token", "state.db", "host.json"):
        (root / kept).write_text(kept)
    (root / "projects" / "app").mkdir(parents=True)
    if installed:
        (root / "runtime.version").write_text(installed + "\n")
    new_install = (f'#!/usr/bin/env bash\nrm -f "{root}/runtime.version"\n'
                   f'echo "new install.sh $*"\nexit {install_code}\n'
                   if install_code else
                   f'#!/usr/bin/env bash\necho "new install.sh $*"\necho "$1" > "{root}/runtime.version"\n')
    tar_path = _archive(tmp_path, latest, new_install)
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(f'#!/bin/sh\necho "$*" >> "{docker_log}"\nexit {pull_code}\n')
    fake_docker.chmod(0o755)
    script = (GET.read_text().replace("/opt/eggie", str(root))
              .replace("/usr/bin/docker", str(fake_docker)))
    curl = ('for last; do :; done\n'
            'case "$last" in\n'
            '*/release.json) echo \'{"api": 1}\' ;;\n'
            f'*) cp "{tar_path}" "$4" ;;\n'
            'esac\n')
    environ = _bin(tmp_path, dpkg="exit 0\n", curl=curl, git=_git_listing(tmp_path, [latest]))
    for name in ("EGGIE_RUNTIME_REF", "EGGIE_RUNTIME_REPAIR",
                 "EGGIE_RUNTIME_API", "EGGIE_RUNTIME_UPDATE", "EGGIE_RUNTIME_URL"):
        environ.pop(name, None)
    environ["EGGIE_RUNTIME_API"] = "1"
    if update:
        environ["EGGIE_RUNTIME_UPDATE"] = "1"
    result = subprocess.run(["bash", "-c", script], env=environ,
                            capture_output=True, text=True)
    docker = docker_log.read_text() if docker_log.exists() else ""
    return result, root, docker


def test_an_update_to_the_ref_already_installed_changes_nothing(tmp_path):
    result, root, docker = _update_run(tmp_path, installed="runtime-v0.2.0", latest="runtime-v0.2.0")
    assert result.returncode == 0, result.stderr
    assert (root / "runtime" / "keep-me").exists()
    assert "install.sh" not in result.stdout
    assert docker == ""


def test_an_update_pulls_the_new_images_before_touching_the_installed_runtime(tmp_path):
    result, root, docker = _update_run(tmp_path, pull_code=1)
    assert result.returncode != 0
    assert "pull" in docker and "stack.next.yml" in docker and "--profile tunnel" in docker
    assert (root / "runtime" / "keep-me").exists(), "a failed pull must leave the runtime alone"
    assert (root / "runtime.version").read_text().strip() == "runtime-v0.1.0"
    assert not (root / "stack.next.yml").exists()


def test_a_successful_update_replaces_the_runtime_and_drops_the_previous_one(tmp_path):
    result, root, _ = _update_run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "new install.sh runtime-v0.2.0" in result.stdout
    assert not (root / "runtime" / "keep-me").exists()
    assert not (root / "runtime.prev").exists()
    assert (root / "runtime.version").read_text().strip() == "runtime-v0.2.0"
    for kept in ("api.token", "state.db", "host.json"):
        assert (root / kept).read_text() == kept, f"an update must not touch {kept}"
    assert (root / "projects" / "app").is_dir()


def test_a_failed_install_rolls_back_to_the_previous_runtime(tmp_path):
    result, root, _ = _update_run(tmp_path, install_code=1)
    assert result.returncode != 0
    assert "old install.sh runtime-v0.1.0" in result.stdout
    assert (root / "runtime" / "keep-me").exists()
    assert (root / "runtime.version").read_text().strip() == "runtime-v0.1.0"
    assert not (root / "runtime.prev").exists()


def test_every_install_records_where_the_runtime_came_from(tmp_path):
    result, root, _ = _update_run(tmp_path, update=False)
    assert result.returncode == 0, result.stderr
    env = (root / "runtime.env").read_text()
    assert "EGGIE_RUNTIME_REPO=https://github.com/eggie-io/eggie\n" in env
    assert ("EGGIE_RUNTIME_URL=https://github.com/eggie-io/eggie"
            "/raw/main/runtime/install/get.sh\n") in env


def test_get_sh_holds_the_update_lock():
    assert "flock" in GET.read_text() and "/opt/eggie/update.lock" in GET.read_text()
```

- [ ] **Step 2: Run them to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/test_get_sh.py -q`
Expected: the new update-mode tests FAIL.

- [ ] **Step 3: Implement** — replace `main` in `runtime/install/get.sh`:

```bash
main() {
  export DEBIAN_FRONTEND=noninteractive
  local missing=() pkg
  for pkg in ca-certificates curl git; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  if (( ${#missing[@]} )); then
    if ! apt-get update || ! apt-get install -y "${missing[@]}"; then
      echo "could not install ${missing[@]}: the Ubuntu package mirrors may be unreachable" >&2
      exit 1
    fi
  fi

  mkdir -p /opt/eggie
  # The boot unit and a host-started update may run at the same moment.
  exec 9>/opt/eggie/update.lock
  flock 9

  local ref installed="" update=0
  [[ "${EGGIE_RUNTIME_UPDATE:-}" == 1 ]] && update=1
  [[ -s "$MARKER" ]] && installed="$(cat "$MARKER")"
  ref="$(resolve_ref "$REPO" "$MARKER")"
  if (( update )) && [[ "$ref" == "$installed" ]]; then
    echo "Eggie runtime $ref is already installed"
    return 0
  fi
  echo "installing Eggie runtime $ref"

  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp:-}"; rm -f /opt/eggie/stack.next.yml' EXIT
  if ! curl -fsSL "$REPO/archive/$ref.tar.gz" -o "$tmp/runtime.tar.gz"; then
    echo "could not download Eggie runtime $ref from $REPO" >&2
    exit 1
  fi
  if ! tar -xzf "$tmp/runtime.tar.gz" -C "$tmp" --strip-components=1 --wildcards '*/runtime/' 2>/dev/null; then
    echo "$ref of $REPO is not a readable archive or has no runtime/ directory" >&2
    exit 1
  fi
  if [[ ! -f "$tmp/runtime/install/install.sh" ]]; then
    echo "$ref of $REPO has no runtime/install/install.sh" >&2
    exit 1
  fi
  printf 'EGGIE_RUNTIME_URL=%s\nEGGIE_RUNTIME_REPO=%s\n' \
    "${EGGIE_RUNTIME_URL:-$REPO/raw/main/runtime/install/get.sh}" "$REPO" > /opt/eggie/runtime.env

  # Next to /opt/eggie/.env so compose reads the docker GID the stack needs.
  if (( update )) && [[ -n "$installed" ]]; then
    install -m 644 "$tmp/runtime/stack.yml" /opt/eggie/stack.next.yml
    if ! /usr/bin/docker compose -f /opt/eggie/stack.next.yml --profile tunnel pull; then
      echo "could not download the images for Eggie runtime $ref; staying on $installed" >&2
      exit 1
    fi
  fi

  rm -rf "$RUNTIME_DIR.prev"
  if (( update )) && [[ -n "$installed" && -d "$RUNTIME_DIR" ]]; then
    mv "$RUNTIME_DIR" "$RUNTIME_DIR.prev"
  else
    # Replaced, not merged: a file dropped from the runtime must not linger.
    rm -rf "$RUNTIME_DIR"
  fi
  mv "$tmp/runtime" "$RUNTIME_DIR"
  chmod 755 "$RUNTIME_DIR"

  local args=("$ref")
  if [[ "${EGGIE_RUNTIME_REPAIR:-}" == 1 ]]; then
    args+=(--repair)
  fi
  if ! bash "$RUNTIME_DIR/install/install.sh" "${args[@]}"; then
    if [[ -d "$RUNTIME_DIR.prev" ]]; then
      echo "Eggie runtime $ref did not install; going back to $installed" >&2
      rm -rf "$RUNTIME_DIR"
      mv "$RUNTIME_DIR.prev" "$RUNTIME_DIR"
      bash "$RUNTIME_DIR/install/install.sh" "$installed" || true
    fi
    exit 1
  fi
  rm -rf "$RUNTIME_DIR.prev"
}
```

Note: `RUNTIME_DIR.prev` expands to `/opt/eggie/runtime.prev`. The existing `test_a_successful_install_exits_zero_and_hands_install_sh_the_ref` must still pass (no update mode, no docker call).

- [ ] **Step 4: Run the file**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/test_get_sh.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/install/get.sh tests/runtime/test_get_sh.py
git commit -m "get.sh update mode stages, swaps and rolls back"
```

---

### Task 4: The boot-time updater

**Files:**
- Create: `runtime/install/lib/boot-update.sh`
- Create: `runtime/install/systemd/eggie-update.service`
- Modify: `runtime/install/install.sh` (step 12 area)
- Create: `tests/runtime/test_boot_update.py`
- Modify: `tests/runtime/test_install_shell.py` (append)

**Interfaces:**
- Consumes: `/opt/eggie/host.json` (`{"supported_api": [1, 2]}`, written by Task 5), `/opt/eggie/runtime/release.json` (Task 1), `/opt/eggie/runtime.env` (Task 3), `get.sh` update mode (Task 3).
- Produces: `accepted_api` (bash function, prints `1,2` or fails), the enabled `eggie-update.service`.

- [ ] **Step 1: Write the failing tests** — `tests/runtime/test_boot_update.py`:

```python
"""The boot unit's choice of what to accept, and what it hands get.sh."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOT = ROOT / "runtime" / "install" / "lib" / "boot-update.sh"


def _root(tmp_path, *, host_json=None, release='{"api": 1}', installed="runtime-v0.1.0"):
    root = tmp_path / "opt-eggie"
    (root / "runtime").mkdir(parents=True)
    if host_json is not None:
        (root / "host.json").write_text(host_json)
    if release is not None:
        (root / "runtime" / "release.json").write_text(release)
    if installed:
        (root / "runtime.version").write_text(installed + "\n")
    (root / "runtime.env").write_text(
        "EGGIE_RUNTIME_URL=https://example.invalid/get.sh\n"
        "EGGIE_RUNTIME_REPO=https://example.invalid/repo\n")
    return root


def _accepted(tmp_path, **kw):
    root = _root(tmp_path, **kw)
    script = BOOT.read_text().replace("/opt/eggie", str(root))
    return subprocess.run(["bash", "-c", script + "\naccepted_api"],
                          env={**os.environ, "EGGIE_BOOT_UPDATE_SOURCED": "1"},
                          capture_output=True, text=True)


def test_boot_update_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(BOOT)]).returncode == 0


def test_the_hosts_list_is_what_gets_accepted(tmp_path):
    result = _accepted(tmp_path, host_json='{"supported_api": [1, 2]}')
    assert result.stdout.strip() == "1,2"


def test_without_a_host_the_installed_api_is_kept(tmp_path):
    result = _accepted(tmp_path, host_json=None, release='{"api": 3}')
    assert result.stdout.strip() == "3"


def test_a_malformed_host_file_falls_back_to_the_installed_api(tmp_path):
    result = _accepted(tmp_path, host_json='{"supported_api": "all"}', release='{"api": 1}')
    assert result.stdout.strip() == "1"


def test_nothing_to_go_on_is_a_failure_not_an_empty_list(tmp_path):
    result = _accepted(tmp_path, host_json=None, release=None)
    assert result.returncode != 0
    assert result.stdout.strip() == ""


def _run(tmp_path, *, installed="runtime-v0.1.0"):
    root = _root(tmp_path, host_json='{"supported_api": [1]}', installed=installed)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    # The fetched "get.sh" reports what it was handed.
    curl.write_text("#!/bin/sh\necho 'echo \"url=$EGGIE_RUNTIME_URL api=$EGGIE_RUNTIME_API "
                    "update=$EGGIE_RUNTIME_UPDATE repo=$EGGIE_RUNTIME_REPO ref=$EGGIE_RUNTIME_REF\"'\n")
    curl.chmod(0o755)
    script = BOOT.read_text().replace("/opt/eggie", str(root))
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "EGGIE_RUNTIME_REF": "leaked"}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


def test_the_update_runs_get_sh_in_update_mode_from_the_recorded_source(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert ("url=https://example.invalid/get.sh api=1 update=1 "
            "repo=https://example.invalid/repo ref=") in result.stdout


def test_a_vm_installed_from_a_branch_is_left_alone(tmp_path):
    result = _run(tmp_path, installed="feature/x")
    assert result.returncode == 0
    assert "update=1" not in result.stdout
```

Append to `tests/runtime/test_install_shell.py`:

```python
def test_install_enables_the_boot_update_without_running_it_now():
    # Starting it here would run an update inside every install.
    text = INSTALL.read_text()
    assert "systemctl enable eggie-update.service" in text
    assert "enable --now eggie-update" not in text
    assert "start eggie-update" not in text
```

- [ ] **Step 2: Run to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime/test_boot_update.py tests/runtime/test_install_shell.py -q`
Expected: FAIL (file missing, install.sh lacks the unit).

- [ ] **Step 3: Create `runtime/install/lib/boot-update.sh`**

```bash
#!/usr/bin/env bash
# Run at boot by eggie-update.service: moves this VM to the newest runtime
# release its host accepts. Any failure leaves the installed runtime running.
set -euo pipefail

# accepted_api: the api numbers the host last declared, else the installed
# release's own -- a VM with no host never changes api.
accepted_api() {
  local api=""
  if [[ -s /opt/eggie/host.json ]]; then
    api="$(tr -d ' \n' < /opt/eggie/host.json | sed -n 's/.*"supported_api":\[\([0-9,]*\)\].*/\1/p')"
  fi
  if [[ ! "$api" =~ ^[0-9]+(,[0-9]+)*$ && -s /opt/eggie/runtime/release.json ]]; then
    api="$(tr -d ' \n' < /opt/eggie/runtime/release.json | sed -n 's/.*"api":\([0-9][0-9]*\).*/\1/p')"
  fi
  [[ "$api" =~ ^[0-9]+(,[0-9]+)*$ ]] || return 1
  echo "$api"
}

main() {
  local installed api script attempt
  installed="$(cat /opt/eggie/runtime.version 2>/dev/null || true)"
  # A branch was pinned by hand; moving it to a tag would undo that.
  if [[ ! "$installed" =~ ^runtime-v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "installed runtime '$installed' is not a release; not updating"
    return 0
  fi
  if ! api="$(accepted_api)"; then
    echo "no api list to update against; not updating" >&2
    return 1
  fi
  # shellcheck disable=SC1091
  source /opt/eggie/runtime.env
  # The network may come up after this unit starts.
  for attempt in 1 2 3 4 5 6; do
    script="$(curl -fsSL "$EGGIE_RUNTIME_URL")" && break
    script=""
    sleep 10
  done
  if [[ -z "$script" ]]; then
    echo "could not download the Eggie updater from $EGGIE_RUNTIME_URL" >&2
    return 1
  fi
  env -u EGGIE_RUNTIME_REF -u EGGIE_RUNTIME_REPAIR \
    EGGIE_RUNTIME_UPDATE=1 EGGIE_RUNTIME_API="$api" \
    EGGIE_RUNTIME_URL="$EGGIE_RUNTIME_URL" EGGIE_RUNTIME_REPO="$EGGIE_RUNTIME_REPO" \
    bash -c "$script"
}

[[ -n "${EGGIE_BOOT_UPDATE_SOURCED:-}" ]] || main "$@"
```

- [ ] **Step 4: Create `runtime/install/systemd/eggie-update.service`**

```ini
[Unit]
Description=Update the Eggie runtime to the newest release this machine's host accepts
After=network-online.target docker.service
Wants=docker.service

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/eggie/runtime/install/lib/boot-update.sh
TimeoutStartSec=30min

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Install it from `install.sh`** — directly after the step-12 GitHub block (before step 13), add:

```bash
# 12b. the boot-time updater. Enabled only: starting it here would run an
# update inside this install.
install -m 644 "$INSTALL_DIR/systemd/eggie-update.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable eggie-update.service
```

- [ ] **Step 6: Run the tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/runtime -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add runtime/install/lib/boot-update.sh runtime/install/systemd/eggie-update.service \
  runtime/install/install.sh tests/runtime/test_boot_update.py tests/runtime/test_install_shell.py
git commit -m "Update the runtime at boot to the newest release the host accepts"
```

---

### Task 5: The host declares its API range and can ask for an update

**Files:**
- Modify: `host/core/constants.py` (add `HOST_JSON`)
- Create: `host/core/runtime_update.py`
- Modify: `host/core/bootstrap.py`
- Test: `tests/host/test_bootstrap.py`, create `tests/host/test_runtime_update.py`, append to `tests/runtime/test_boot_update.py`

**Interfaces:**
- Produces: `constants.HOST_JSON = f"{GUEST_ROOT}/host.json"`; `runtime_update.declare_supported(provider) -> bool` (never raises on a failed exec; returns `Completed.ok`); `bootstrap(provider, *, source=None, repair=False, update=False)` — `update=True` runs the installer even when installed and adds `EGGIE_RUNTIME_UPDATE=1`; every run carries `EGGIE_RUNTIME_API=<sorted SUPPORTED_API joined by ",">`; the stub exports `EGGIE_RUNTIME_URL` to `get.sh`.

- [ ] **Step 1: Write the failing tests** — append to `tests/host/test_bootstrap.py`:

```python
def test_every_run_tells_the_installer_which_apis_this_host_speaks():
    p = FakeProvider()
    bootstrap(p)
    expected = ",".join(str(n) for n in sorted(constants.SUPPORTED_API))
    assert f"EGGIE_RUNTIME_API={expected}" in _command(p)
    assert "EGGIE_RUNTIME_UPDATE" not in _command(p)


def test_an_update_runs_on_an_installed_runtime_in_update_mode():
    p = FakeProvider(installed=True)
    bootstrap(p, update=True)
    assert "EGGIE_RUNTIME_UPDATE=1" in _command(p)


def test_the_stub_hands_get_sh_the_url_it_came_from(tmp_path):
    env = _fake_curl(tmp_path, "echo 'echo \"from=$EGGIE_RUNTIME_URL\"'\n")
    result = _run_stub(tmp_path, env)
    assert result.stdout.strip() == "from=https://example.invalid/get.sh"
```

Create `tests/host/test_runtime_update.py`:

```python
import base64
import json
import re

from host.core import constants
from host.core.provider import Completed
from host.core.runtime_update import declare_supported


class FakeProvider:
    def __init__(self, ok=True):
        self.execs = []
        self._ok = ok

    def exec(self, argv, *, root=False):
        self.execs.append((argv, root))
        return Completed(0 if self._ok else 1, "", "")


def test_the_host_writes_its_supported_apis_where_the_boot_update_reads_them():
    p = FakeProvider()
    assert declare_supported(p) is True
    ((argv, root),) = p.execs
    assert root is True
    command = argv[2]
    written = json.loads(base64.b64decode(re.search(r"echo (\S+) \| base64 -d", command)[1]))
    assert written == {"supported_api": sorted(constants.SUPPORTED_API)}
    assert command.rstrip().endswith(constants.HOST_JSON)


def test_a_failed_write_is_reported_not_raised():
    assert declare_supported(FakeProvider(ok=False)) is False
```

Append to `tests/runtime/test_boot_update.py` (keeps the two sides spelling the path the same):

```python
def test_the_boot_update_reads_the_file_the_host_writes():
    from host.core import constants
    assert constants.HOST_JSON in BOOT.read_text()
```

- [ ] **Step 2: Run to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/test_bootstrap.py tests/host/test_runtime_update.py tests/runtime/test_boot_update.py -q`
Expected: FAIL (no `update` kwarg, no module, no constant).

- [ ] **Step 3: Implement**

`host/core/constants.py`, after `RUNTIME_MARKER`:

```python
# Written by the host on every connect; the VM's boot-time updater reads it to
# stay on releases this host can drive.
HOST_JSON = f"{GUEST_ROOT}/host.json"
```

`host/core/runtime_update.py`:

```python
from __future__ import annotations

import base64
import json

from . import constants


def declare_supported(provider) -> bool:
    """Tell the VM which API numbers this host speaks."""
    body = json.dumps({"supported_api": sorted(constants.SUPPORTED_API)})
    encoded = base64.b64encode(body.encode()).decode("ascii")
    # Through a temp file: the boot unit may read it at any moment.
    tmp = f"{constants.HOST_JSON}.tmp"
    result = provider.exec(
        ["bash", "-lc", f"echo {encoded} | base64 -d > {tmp} && mv {tmp} {constants.HOST_JSON}"],
        root=True)
    return result.ok
```

The test extracts the tail after `mv … `; `command.rstrip().endswith(constants.HOST_JSON)` holds.

`host/core/bootstrap.py` — change the stub's last line and `bootstrap`:

```python
# (in _STUB, replace the final `bash -c "$script"` line with)
EGGIE_RUNTIME_URL="$1" bash -c "$script"
```

```python
def bootstrap(provider, *, source: str | None = None, repair: bool = False,
              update: bool = False) -> None:
    """Install the runtime in the VM unless it is already there.

    `repair` reinstalls regardless and tells the installer to keep the ref it
    has and recreate the api container. `update` moves to the newest release
    speaking an API this host supports, leaving an up-to-date VM untouched.
    """
    if not repair and not update and _installed(provider):
        return
    url = _shell_safe(
        source or os.environ.get("EGGIE_RUNTIME_URL") or constants.RUNTIME_URL,
        "EGGIE_RUNTIME_URL")
    apis = ",".join(str(n) for n in sorted(constants.SUPPORTED_API))
    assignments = [f"EGGIE_RUNTIME_API={apis}"]
    ref = os.environ.get("EGGIE_RUNTIME_REF")
    if ref:
        assignments.append(f"EGGIE_RUNTIME_REF={_shell_safe(ref, 'EGGIE_RUNTIME_REF')}")
    if repair:
        assignments.append("EGGIE_RUNTIME_REPAIR=1")
    if update:
        assignments.append("EGGIE_RUNTIME_UPDATE=1")
    # ... rest unchanged
```

- [ ] **Step 4: Run the host and runtime suites**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host tests/runtime -q`
Expected: all PASS (including `test_no_dead_modules` once Task 6 imports the module — if it fails here only on `runtime_update` being unreachable, proceed; Task 6 wires it. Otherwise fix before committing).

- [ ] **Step 5: Commit**

```bash
git add host/core/constants.py host/core/runtime_update.py host/core/bootstrap.py \
  tests/host/test_bootstrap.py tests/host/test_runtime_update.py tests/runtime/test_boot_update.py
git commit -m "Host declares its API range and can run the installer in update mode"
```

---

### Task 6: `connect_step` updates an incompatible runtime

**Files:**
- Modify: `host/core/install.py` (`connect_step`, `default_steps` connect step, new `update_runtime`, `_connect`)
- Test: `tests/host/test_connect_step.py`

**Interfaces:**
- Consumes: `bootstrap(update=True)`, `declare_supported` (Task 5).
- Produces: `connect_step(provider, *, client=None, reconnect=None, update=None, sleep=time.sleep) -> str | None`; `update` is a zero-arg callable returning a fresh client (or None) and may raise `BootstrapError`. `install.update_runtime(provider) -> ApiClient`. `install.reconnect_runtime(provider) -> ApiClient` (the renamed `_reconnect`, public for the desktop).

- [ ] **Step 1: Write the failing tests** — in `tests/host/test_connect_step.py`, replace `test_an_api_on_another_version_is_reported_and_never_repaired` with:

```python
from host.core.bootstrap import BootstrapError

UNSUPPORTED = max(constants.SUPPORTED_API) + 1


def test_an_api_on_another_version_with_no_way_to_update_is_reported_and_never_repaired():
    reconnects = []
    with pytest.raises(ApiIncompatible) as excinfo:
        connect_step(None, client=FakeClient(health={"status": "ok", "api": UNSUPPORTED}),
                     reconnect=lambda: reconnects.append("reconnect"))
    assert str(UNSUPPORTED) in str(excinfo.value), "support needs the number"
    assert reconnects == [], "reinstalling the same runtime cannot change its API"


def test_an_api_on_another_version_is_updated_once():
    updates = []

    def update():
        updates.append("update")
        return FakeClient("0.2.0")

    message = connect_step(None, client=FakeClient(health={"status": "ok", "api": UNSUPPORTED}),
                           update=update)
    assert updates == ["update"]
    assert "updated" in message


def test_an_api_still_incompatible_after_the_update_is_reported():
    with pytest.raises(ApiIncompatible):
        connect_step(None, client=FakeClient(health={"status": "ok", "api": UNSUPPORTED}),
                     update=lambda: FakeClient(health={"status": "ok", "api": UNSUPPORTED}))


def test_a_failed_update_carries_the_installers_own_words():
    def update():
        raise BootstrapError("no runtime-v* release in repo speaks api 1")

    with pytest.raises(ApiIncompatible) as excinfo:
        connect_step(None, client=FakeClient(health={"status": "ok", "api": UNSUPPORTED}),
                     update=update)
    assert "speaks api 1" in str(excinfo.value)


def test_a_supported_api_is_never_updated():
    updates = []
    assert connect_step(None, client=FakeClient("0.1.0"),
                        update=lambda: updates.append("update")) is None
    assert updates == []
```

- [ ] **Step 2: Run to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/test_connect_step.py -q`
Expected: FAIL (`update` unexpected kwarg).

- [ ] **Step 3: Implement** in `host/core/install.py`:

```python
def _incompatible(api) -> str:
    from host.core import constants
    supported = ", ".join(str(n) for n in sorted(constants.SUPPORTED_API))
    return ("This app and the Eggie service inside the virtual machine are "
            "versions that cannot work together.\n"
            f"service API {api}, app supports {supported}")


def connect_step(provider, *, client=None, reconnect=None, update=None,
                 sleep=time.sleep):
    """Check the API speaks a version this host supports and accepts this
    host's token.

    `update` moves the runtime to a release speaking a supported API and
    returns a client for it. `reconnect` reinstalls the runtime in repair mode
    -- recreating the API so it re-reads its token -- and returns a client
    holding the token the VM has now. Returning None keeps the current client.
    """
    from host.client import ApiClient, ApiError
    from host.core import constants
    from host.core.bootstrap import BootstrapError

    client = client or ApiClient.for_provider(provider)
    # 0.1.0 APIs predate the field and serve api 1.
    api = _once_serving(client.health, sleep, API_RESTART_TIMEOUT).get("api", 1)
    updated = False
    if api not in constants.SUPPORTED_API:
        if update is None:
            raise ApiIncompatible(_incompatible(api))
        try:
            client = update() or client
        except BootstrapError as e:
            raise ApiIncompatible(
                f"{_incompatible(api)}\nUpdating it did not work:\n{e}") from e
        api = _once_serving(client.health, sleep, API_RESTART_TIMEOUT).get("api", 1)
        if api not in constants.SUPPORTED_API:
            raise ApiIncompatible(_incompatible(api))
        updated = True
    # /health skips the token check; /version is the cheapest route that does not.
    try:
        client.version()
    except ApiError as e:
        # ... existing reconnect block unchanged ...
        return ("The Eggie service in the virtual machine was not accepting "
                "this computer, and has been reconnected.")
    if updated:
        return "The Eggie service in the virtual machine was updated."
    return None
```

Replace `_reconnect` and the connect step:

```python
def reconnect_runtime(provider):
    """Reinstall in repair mode, which recreates the API container so it
    re-reads its token, then dial it with the token the VM holds now (the
    client caches the one it was built with)."""
    from host.client import ApiClient

    _bootstrap(provider, repair=True)
    return ApiClient.for_provider(provider)


def update_runtime(provider):
    """Move the VM to the newest release speaking an API this host supports."""
    from host.client import ApiClient
    from .bootstrap import bootstrap

    bootstrap(provider, update=True)
    return ApiClient.for_provider(provider)


def connect_with_updates(provider):
    from .runtime_update import declare_supported

    declare_supported(provider)
    return connect_step(provider, reconnect=lambda: reconnect_runtime(provider),
                        update=lambda: update_runtime(provider))
```

and in `default_steps`: `step("connect", lambda: connect_with_updates(provider), always_run=True),`. Remove `_reconnect`.

- [ ] **Step 4: Run the host suite**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host tests/test_setup_cli.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add host/core/install.py tests/host/test_connect_step.py
git commit -m "Update an incompatible runtime instead of refusing it"
```

---

### Task 7: The desktop updates an incompatible runtime on its own

**Files:**
- Modify: `host/desktop/view.py` (`route_for`)
- Modify: `host/desktop/api.py` (`home`, `start_runtime_update`, `start_repair`)
- Modify: `host/desktop/ui/index.html`, `host/desktop/ui/app.js`
- Modify: `runtime/web/apps/console/src/screens/NeedsUpdate.tsx`
- Test: `tests/host/desktop/test_view_route.py`, `tests/host/desktop/test_api_home.py`, `tests/host/desktop/test_api_actions.py`, `tests/host/desktop/test_ui_assets.py`

**Interfaces:**
- Consumes: `connect_with_updates(provider)` (Task 6), `declare_supported` (Task 5).
- Produces: route `("update_runtime", "")`; bridge method `start_runtime_update() -> {"job": id}` with job kind `"runtime_update"`; UI screens `runtime-update:running`, `runtime-update:failed`.

- [ ] **Step 1: Write the failing tests**

`tests/host/desktop/test_view_route.py` — replace `test_unsupported_api_is_wrong_not_unreachable`:

```python
def test_an_installed_runtime_on_an_unsupported_api_is_updated():
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          runtime_version="runtime-v9.0.0", api_version=99)
    assert route_for(readiness) == ("update_runtime", "")
```

`tests/host/desktop/test_api_home.py` — append:

```python
class RecordingProvider:
    def __init__(self):
        self.execs = []

    def exec(self, argv, *, root=False):
        from host.core.provider import Completed
        self.execs.append(argv)
        return Completed(0, "", "")


def test_home_declares_the_hosts_apis_once_the_vm_answers(tmp_path):
    from host.core import constants
    provider = RecordingProvider()
    api = DesktopApi(provider, InstallState(tmp_path / "s.json"), push=lambda e: None,
                     probe_fn=lambda p: READY)
    api.home()
    api.home()
    writes = [a for a in provider.execs if constants.HOST_JSON in a[-1]]
    assert len(writes) == 1, "once per session is enough"


def test_home_never_declares_to_a_vm_that_is_not_answering(tmp_path):
    provider = RecordingProvider()
    api = DesktopApi(provider, InstallState(tmp_path / "s.json"), push=lambda e: None,
                     probe_fn=lambda p: Readiness(vm_exists=True))
    api.home()
    assert provider.execs == []
```

`tests/host/desktop/test_api_actions.py` — append:

```python
def test_the_runtime_update_job_reports_failure_in_the_installers_words(tmp_path, monkeypatch):
    from host.core import install
    from host.core.install import ApiIncompatible

    def failing(provider):
        raise ApiIncompatible("no runtime-v* release speaks api 2")

    monkeypatch.setattr(install, "connect_with_updates", failing)
    pushed = []
    api = _api(tmp_path, FakeProvider(), pushed)
    api.start_runtime_update()
    api.jobs.join(timeout=5)
    assert pushed[-1]["kind"] == "runtime_update"
    assert pushed[-1]["type"] == "crashed"
    assert "speaks api 2" in pushed[-1]["message"]
```

`tests/host/desktop/test_ui_assets.py` — append:

```python
def test_the_runtime_update_screens_exist_and_auto_start_once():
    markup = (UI / "index.html").read_text()
    for screen in ("runtime-update:running", "runtime-update:failed"):
        assert f'data-screen="{screen}"' in markup
    script = (UI / "app.js").read_text()
    # Auto-starting on every refresh would loop forever on an update that
    # "succeeds" without fixing the API.
    assert "runtimeUpdateTried" in script
```

- [ ] **Step 2: Run to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/desktop -q`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

`host/desktop/view.py` `route_for` — replace the api line:

```python
    if readiness.api_version not in constants.SUPPORTED_API:
        return ("update_runtime", "")
```

`host/desktop/api.py`:
- In `__init__` add `self._declared = False`.
- In `home()`, right after `readiness = self._probe(self._provider)`:

```python
        if readiness.vm_reachable and not self._declared:
            from host.core.runtime_update import declare_supported
            try:
                self._declared = declare_supported(self._provider)
            except Exception:
                # A hung VM surfaces through the probe on the next refresh.
                pass
```

- Add after `start_repair` and make repair use the same connect:

```python
    def start_runtime_update(self) -> dict:
        from host.core import install

        def work(emit):
            emit({"type": "stage", "stage": "update"})
            install.connect_with_updates(self._provider)
            return {"type": "done"}

        return {"job": self.jobs.start("runtime_update", work)}
```

In `start_repair`, replace `connect_step(self._provider)` with `install.connect_with_updates(self._provider)` (import `from host.core import install`), so a repair on an incompatible runtime also updates it.

`host/desktop/ui/index.html` — add after the `unresponsive` template:

```html
<template data-screen="runtime-update:running">
  <section class="pad centered">
    <span class="badge badge-warm">Updating</span>
    <h1 class="display">Updating Eggie inside the kitchen</h1>
    <p class="lede">This app is newer than what runs inside your machine, so that part
      is catching up. It takes a minute or two. Your projects stay put.</p>
  </section>
</template>

<template data-screen="runtime-update:failed">
  <section class="pad">
    <span class="badge badge-bad">Update didn't finish</span>
    <h1 class="display">The kitchen couldn't catch up</h1>
    <p class="lede">Eggie couldn't update what runs inside your machine, so this app
      can't talk to it yet. Your projects are safe. Check your internet connection and try again.</p>
    <div class="row">
      <button class="btn-primary" data-action="retry-runtime-update">Try again</button>
      <button class="btn-secondary" data-action="doctor">Run Doctor</button>
    </div>
    <details class="log"><summary>Show logs</summary>
      <pre data-field="message"></pre></details>
  </section>
</template>
```

`host/desktop/ui/app.js` — add near the repair handlers:

```js
// Auto-started once per session: an update that "succeeds" without fixing the
// API would otherwise restart itself on every refresh.
let runtimeUpdateTried = false;

async function startRuntimeUpdate() {
  runtimeUpdateTried = true;
  show('runtime-update:running', {});
  await api().start_runtime_update();
}

ACTIONS['retry-runtime-update'] = () => startRuntimeUpdate();
JOB_ACTIONS.add('retry-runtime-update');

window.eggie.handlers.runtime_update = (event) => {
  if (event.type === 'progress' || event.type === 'stage') return;
  if (event.type === 'crashed') return show('runtime-update:failed', { message: event.message });
  refresh();
};
```

and in `refresh()`, before the final `show(...)`:

```js
  if (home.route === 'update_runtime') {
    if (!runtimeUpdateTried) return startRuntimeUpdate();
    return show('runtime-update:failed', { message: home.problem });
  }
```

`runtime/web/apps/console/src/screens/NeedsUpdate.tsx` — replace the lead paragraph text with:

```tsx
        This page and the Eggie service on your computer come from different releases, so they can't safely
        talk to each other. Open the Eggie desktop app — it finishes the update.
```

- [ ] **Step 4: Run tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host -q && (cd runtime/web && npm test -- --run && npm run typecheck)`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add host/desktop runtime/web/apps/console/src/screens/NeedsUpdate.tsx tests/host/desktop
git commit -m "Desktop updates an incompatible runtime on its own"
```

---

### Task 8: Providers name and launch the installer; packaging names match

**Files:**
- Modify: `host/providers/wsl2.py`, `host/providers/lima.py`
- Modify: `tests/host/test_provider_surface.py` (`INSTALL_SURFACE`)
- Modify: `packaging/macos/build.sh` (pkg name), `packaging/windows/installer.iss` (relaunch after silent update)
- Test: `tests/host/test_wsl2.py`, `tests/host/test_lima.py`

**Interfaces:**
- Produces: `provider.installer_asset(version: str) -> str`; `provider.launch_installer(path: Path) -> None`. `LimaProvider.__init__` gains `machine=None` (callable returning e.g. `"arm64"`, default `platform.machine`).

- [ ] **Step 1: Write the failing tests**

`tests/host/test_provider_surface.py`: add `"installer_asset", "launch_installer"` to `INSTALL_SURFACE`.

Append to `tests/host/test_wsl2.py` (use the file's existing FakeRunner pattern; `spawner` records argv):

```python
def test_the_windows_installer_updates_silently_and_closes_the_running_app(tmp_path):
    from pathlib import Path
    from host.providers.wsl2 import Wsl2Provider
    spawned = []
    provider = Wsl2Provider(spawner=spawned.append, arch="amd64")
    assert provider.installer_asset("0.2.0") == "EggieSetup-0.2.0.exe"
    provider.launch_installer(Path("C:/cache/EggieSetup-0.2.0.exe"))
    (argv,) = spawned
    assert argv[0].endswith("EggieSetup-0.2.0.exe")
    assert {"/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NORESTART"} <= set(argv[1:])
```

Append to `tests/host/test_lima.py`:

```python
def test_the_mac_installer_is_the_package_for_this_architecture():
    from pathlib import Path
    from host.providers.lima import LimaProvider
    ran = []

    class Result:
        returncode, stdout, stderr = 0, b"", b""

    provider = LimaProvider(runner=lambda argv: ran.append(argv) or Result(),
                            machine=lambda: "arm64")
    assert provider.installer_asset("0.2.0") == "EggieSetup-0.2.0-arm64.pkg"
    provider.launch_installer(Path("/cache/EggieSetup-0.2.0-arm64.pkg"))
    assert ran == [["open", "/cache/EggieSetup-0.2.0-arm64.pkg"]]
```

- [ ] **Step 2: Run to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/test_provider_surface.py tests/host/test_wsl2.py tests/host/test_lima.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`host/providers/wsl2.py`, in `Wsl2Provider`:

```python
    def installer_asset(self, version: str) -> str:
        return f"EggieSetup-{version}.exe"

    def launch_installer(self, path: Path) -> None:
        # The installer closes this app itself and relaunches it when done
        # (installer.iss); the app must not wait for it.
        self._spawn([str(path), "/SILENT", "/SUPPRESSMSGBOXES",
                     "/CLOSEAPPLICATIONS", "/NORESTART"])
```

`host/providers/lima.py`: add `machine=None` to `__init__`, store `self._machine = machine or platform.machine`, then:

```python
    def installer_asset(self, version: str) -> str:
        # The .pkg is built native-arch: an arm64 package refuses an Intel Mac.
        return f"EggieSetup-{version}-{self._machine()}.pkg"

    def launch_installer(self, path: Path) -> None:
        self._run(["open", str(path)])
```

`packaging/macos/build.sh`: change `pkg="$repo/dist/EggieSetup-$version.pkg"` to

```bash
pkg="$repo/dist/EggieSetup-$version-$(uname -m).pkg"
```

`packaging/windows/installer.iss`: in `[Setup]` add `CloseApplications=force`; in `[Run]` add:

```ini
; A silent run is the app's own Update button: reopen it when done. The entry
; above is skipifsilent, so it never covers this.
Filename: "{app}\setup.exe"; Parameters: "setup"; Flags: nowait runasoriginaluser; Check: WizardSilent
```

Grep docs for `EggieSetup-<version>.pkg` (`docs/building.md`, `docs/release-testing.md`, `host/CLAUDE.md`) and change to `EggieSetup-<version>-<arch>.pkg`.

- [ ] **Step 4: Run tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host -q && TMPDIR=$PWD/.tmp python3 -m pytest tests/test_no_platform_leak.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add host/providers tests/host packaging docs host/CLAUDE.md
git commit -m "Providers name and launch the app installer"
```

---

### Task 9: Finding a newer desktop app

**Files:**
- Modify: `host/core/constants.py` (`HOST_RELEASES_URL`)
- Create: `host/core/app_update.py`
- Create: `tests/host/test_app_update.py`

**Interfaces:**
- Produces:
  - `AppRelease(version: str, url: str, sha256: str)` frozen dataclass.
  - `parse_version(text: str) -> tuple[int, int, int] | None`.
  - `pick_release(releases: list[dict], *, current: str, asset_name: Callable[[str], str]) -> tuple[str, dict, dict] | None` — `(version, installer asset, SHA256SUMS asset)`.
  - `check(*, current: str, asset_name: Callable[[str], str], fetch=_fetch) -> AppRelease | None` — never raises.
  - `fetch(url) -> bytes` signature for the injected fetcher.

- [ ] **Step 1: Write the failing tests** — `tests/host/test_app_update.py`:

```python
"""Choosing a newer desktop app from GitHub's releases listing."""
import hashlib
import json

from host.core import app_update
from host.core.app_update import AppRelease, check, pick_release


def _asset(name):
    return {"name": name, "browser_download_url": f"https://dl.invalid/{name}"}


def _release(tag, *names, draft=False, prerelease=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
            "assets": [_asset(n) for n in names]}


def _exe(version):
    return f"EggieSetup-{version}.exe"


def test_the_highest_host_release_above_this_one_wins_by_number_not_text():
    releases = [_release("host-v0.9.0", _exe("0.9.0"), "SHA256SUMS"),
                _release("host-v0.10.0", _exe("0.10.0"), "SHA256SUMS")]
    version, installer, _ = pick_release(releases, current="0.1.0", asset_name=_exe)
    assert version == "0.10.0"
    assert installer["name"] == "EggieSetup-0.10.0.exe"


def test_runtime_tags_drafts_and_pre_releases_are_ignored():
    releases = [_release("runtime-v9.0.0", _exe("9.0.0"), "SHA256SUMS"),
                _release("host-v2.0.0", _exe("2.0.0"), "SHA256SUMS", draft=True),
                _release("host-v3.0.0", _exe("3.0.0"), "SHA256SUMS", prerelease=True),
                _release("host-v4.0.0-rc1", _exe("4.0.0-rc1"), "SHA256SUMS")]
    assert pick_release(releases, current="0.1.0", asset_name=_exe) is None


def test_this_version_or_older_is_no_update():
    releases = [_release("host-v0.1.0", _exe("0.1.0"), "SHA256SUMS")]
    assert pick_release(releases, current="0.1.0", asset_name=_exe) is None


def test_a_release_without_this_machines_installer_or_checksums_is_skipped():
    releases = [_release("host-v0.3.0", "EggieSetup-0.3.0-arm64.pkg", "SHA256SUMS"),
                _release("host-v0.2.0", _exe("0.2.0")),
                _release("host-v0.1.5", _exe("0.1.5"), "SHA256SUMS")]
    version, _, _ = pick_release(releases, current="0.1.0", asset_name=_exe)
    assert version == "0.1.5"


def test_check_returns_the_installer_and_its_published_digest():
    digest = hashlib.sha256(b"x").hexdigest()
    listing = json.dumps([_release("host-v0.2.0", _exe("0.2.0"), "SHA256SUMS")]).encode()
    sums = f"{'0' * 64}  EggieSetup-0.2.0-arm64.pkg\n{digest}  EggieSetup-0.2.0.exe\n".encode()
    pages = {app_update.RELEASES_URL: listing, "https://dl.invalid/SHA256SUMS": sums}
    assert check(current="0.1.0", asset_name=_exe, fetch=pages.__getitem__) == \
        AppRelease("0.2.0", "https://dl.invalid/EggieSetup-0.2.0.exe", digest)


def test_a_checksum_file_not_naming_the_installer_is_no_update():
    listing = json.dumps([_release("host-v0.2.0", _exe("0.2.0"), "SHA256SUMS")]).encode()
    pages = {app_update.RELEASES_URL: listing, "https://dl.invalid/SHA256SUMS": b""}
    assert check(current="0.1.0", asset_name=_exe, fetch=pages.__getitem__) is None


def test_an_unreachable_github_is_no_update():
    def offline(url):
        raise OSError("network unreachable")
    assert check(current="0.1.0", asset_name=_exe, fetch=offline) is None


def test_a_listing_that_is_not_a_list_is_no_update():
    assert check(current="0.1.0", asset_name=_exe,
                 fetch=lambda url: b'{"message": "API rate limit exceeded"}') is None
```

- [ ] **Step 2: Run to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/test_app_update.py -q`
Expected: FAIL (no module).

- [ ] **Step 3: Implement**

`host/core/constants.py`, after `APP_VERSION`:

```python
# Desktop app releases are tagged host-vX.Y.Z on this repository.
HOST_RELEASES_URL = "https://api.github.com/repos/eggie-io/eggie/releases"
```

`host/core/app_update.py`:

```python
"""Is there a newer desktop app, and where is its installer?"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from . import constants

RELEASES_URL = constants.HOST_RELEASES_URL
_TAG = re.compile(r"host-v(\d+\.\d+\.\d+)")
_SUMS = "SHA256SUMS"


@dataclass(frozen=True)
class AppRelease:
    version: str
    url: str
    sha256: str


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(n) for n in match.groups()) if match else None


def pick_release(releases, *, current: str, asset_name: Callable[[str], str]):
    floor = parse_version(current) or (0, 0, 0)
    found = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = _TAG.fullmatch(release.get("tag_name", ""))
        if not tag or parse_version(tag[1]) <= floor:
            continue
        assets = {a.get("name"): a for a in release.get("assets", [])}
        installer, sums = assets.get(asset_name(tag[1])), assets.get(_SUMS)
        if installer and sums:
            found.append((parse_version(tag[1]), tag[1], installer, sums))
    if not found:
        return None
    _, version, installer, sums = max(found, key=lambda f: f[0])
    return version, installer, sums


def _digest_for(sums: str, name: str) -> str | None:
    for line in sums.splitlines():
        parts = line.split()
        # sha256sum marks binary mode with a leading "*" on the name.
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            return parts[0]
    return None


def _fetch(url: str) -> bytes:
    from urllib.request import Request, urlopen
    # GitHub's API refuses requests without a User-Agent.
    request = Request(url, headers={"User-Agent": f"eggie/{constants.APP_VERSION}",
                                    "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=10) as response:
        return response.read()


def check(*, current: str, asset_name: Callable[[str], str],
          fetch: Callable[[str], bytes] = _fetch) -> AppRelease | None:
    """The newest installable release above `current`, or None. Never raises:
    an update check that fails is the same as no update."""
    try:
        releases = json.loads(fetch(RELEASES_URL))
        if not isinstance(releases, list):
            return None
        picked = pick_release(releases, current=current, asset_name=asset_name)
        if picked is None:
            return None
        version, installer, sums = picked
        digest = _digest_for(fetch(sums["browser_download_url"]).decode("utf-8", "replace"),
                             installer["name"])
        if digest is None:
            return None
        return AppRelease(version, installer["browser_download_url"], digest)
    except Exception:
        return None
```

- [ ] **Step 4: Run tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/test_app_update.py tests/host/test_host_dependencies.py -q`
Expected: PASS. (`tests/host/test_no_dead_modules.py` reports `host.core.app_update` unreachable until Task 10 imports it; that one failure is expected between these two tasks.)

- [ ] **Step 5: Commit**

```bash
git add host/core/constants.py host/core/app_update.py tests/host/test_app_update.py
git commit -m "Find a newer desktop app among host-v* releases"
```

---

### Task 10: The Update button

**Files:**
- Modify: `host/desktop/api.py` (constructor, `home`, `check_app_update`, `start_app_update`)
- Modify: `host/desktop/__main__.py` (start the background check)
- Modify: `host/desktop/ui/index.html`, `host/desktop/ui/app.js`
- Test: create `tests/host/desktop/test_api_app_update.py`; modify `tests/host/desktop/test_ui_assets.py`

**Interfaces:**
- Consumes: `app_update.check`, `AppRelease` (Task 9); `provider.installer_asset`, `provider.launch_installer` (Task 8); `host.core.download.fetch(Image, dest, on_progress=)`, `host.core.images.Image(url, sha256)`.
- Produces: `DesktopApi(..., app_update_fn=None, quit_app=None)`; `start_app_update_check()` (spawns a daemon thread); bridge methods `check_app_update() -> {"available": str, "app_version": str, "runtime_version": ""}` and `start_app_update() -> {"job": id} | {"ok": False}`; job kind `"app_update"`; `home()` gains `"app_update": "<version>" or ""`.

- [ ] **Step 1: Write the failing tests** — `tests/host/desktop/test_api_app_update.py`:

```python
"""The desktop's own update: found, downloaded, verified, launched, closed."""
from __future__ import annotations

import hashlib

from host.core.app_update import AppRelease
from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi


class FakeProvider:
    def __init__(self):
        self.launched = []

    def installer_asset(self, version):
        return f"EggieSetup-{version}.exe"

    def launch_installer(self, path):
        self.launched.append(path)


def _api(tmp_path, release, *, pushed=None, quits=None, provider=None):
    return DesktopApi(provider or FakeProvider(), InstallState(tmp_path / "s.json"),
                      push=(pushed.append if pushed is not None else lambda e: None),
                      probe_fn=lambda p: Readiness(),
                      install_dir_factory=lambda: tmp_path / "eggie" / "vm",
                      app_update_fn=lambda: release,
                      quit_app=(lambda: quits.append(True)) if quits is not None else lambda: None)


def test_home_shows_an_update_only_after_one_was_found(tmp_path):
    api = _api(tmp_path, AppRelease("0.2.0", "https://dl.invalid/x.exe", "0" * 64))
    assert api.home()["app_update"] == ""
    api.check_app_update()
    assert api.home()["app_update"] == "0.2.0"


def test_no_release_means_nothing_to_offer(tmp_path):
    api = _api(tmp_path, None)
    assert api.check_app_update()["available"] == ""
    assert api.start_app_update() == {"ok": False}


def test_the_installer_is_verified_launched_and_the_app_closes(tmp_path, monkeypatch):
    body = b"installer"
    release = AppRelease("0.2.0", "https://dl.invalid/EggieSetup-0.2.0.exe",
                         hashlib.sha256(body).hexdigest())

    import io
    from host.core import download
    monkeypatch.setattr(download, "_default_opener",
                        lambda url, start: (io.BytesIO(body), len(body)))
    provider, pushed, quits = FakeProvider(), [], []
    api = _api(tmp_path, release, pushed=pushed, quits=quits, provider=provider)
    api.check_app_update()
    api.start_app_update()
    api.jobs.join(timeout=5)
    (path,) = provider.launched
    assert path.read_bytes() == body
    assert path.name == "EggieSetup-0.2.0.exe"
    assert quits == [True]


def test_a_download_that_does_not_match_its_digest_is_never_launched(tmp_path, monkeypatch):
    import io
    from host.core import download
    monkeypatch.setattr(download, "_default_opener",
                        lambda url, start: (io.BytesIO(b"tampered"), 8))
    provider, pushed, quits = FakeProvider(), [], []
    api = _api(tmp_path, AppRelease("0.2.0", "https://dl.invalid/EggieSetup-0.2.0.exe", "0" * 64),
               pushed=pushed, quits=quits, provider=provider)
    api.check_app_update()
    api.start_app_update()
    api.jobs.join(timeout=5)
    assert provider.launched == []
    assert quits == []
    assert pushed[-1]["type"] == "crashed"
```

Note: `download.fetch`'s `opener` default is bound at definition time, so `start_app_update` must call `download.fetch(..., opener=download._default_opener)` looked up at call time, or the monkeypatch above won't apply. Implement it that way.

`tests/host/desktop/test_ui_assets.py`: in `test_every_home_state_has_a_template` replace `"updates-unavailable"` with `"updates"`, and append:

```python
def test_every_home_state_can_offer_the_app_update():
    markup = (UI / "index.html").read_text()
    for screen in ("home:not_installed", "home:stopped", "home:running", "home:wrong"):
        start = markup.index(f'data-screen="{screen}"')
        end = markup.index("</template>", start)
        assert 'data-when="app_update"' in markup[start:end], screen
```

- [ ] **Step 2: Run to see them fail**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host/desktop -q`
Expected: FAIL.

- [ ] **Step 3: Implement `host/desktop/api.py`**

Constructor gains `app_update_fn=None, quit_app=None`:

```python
        self._app_update_fn = app_update_fn or self._default_app_update
        self._quit_app = quit_app or self._default_quit
        self._app_release = None
```

```python
    def _default_app_update(self):
        from host.core import app_update
        return app_update.check(current=constants.APP_VERSION,
                                asset_name=self._provider.installer_asset)

    @staticmethod
    def _default_quit():
        import webview
        webview.windows[0].destroy()

    def start_app_update_check(self) -> None:
        """Once per launch, off the window's thread; Home picks it up on its next refresh."""
        import threading

        def run():
            self._app_release = self._app_update_fn()

        threading.Thread(target=run, daemon=True).start()
```

In `home()`'s returned dict add:

```python
            "app_update": self._app_release.version if self._app_release else "",
```

Bridge methods:

```python
    def check_app_update(self) -> dict:
        self._app_release = self._app_update_fn()
        return {"available": self._app_release.version if self._app_release else "",
                "app_version": constants.APP_VERSION}

    def start_app_update(self) -> dict:
        from host.core import download
        from host.core.images import Image

        release = self._app_release
        if release is None:
            return {"ok": False}
        dest = self._install_dir_factory().parent / "cache" / release.url.rsplit("/", 1)[-1]

        def work(emit):
            def on_progress(done, total):
                emit({"type": "progress", "done": done, "total": total})

            path = download.fetch(Image(release.url, release.sha256), dest,
                                  opener=download._default_opener, on_progress=on_progress)
            self._provider.launch_installer(path)
            self._quit_app()
            return {"type": "done"}

        return {"job": self.jobs.start("app_update", work)}
```

`host/desktop/__main__.py` `run()`: after `api.resumed = resumed`, add `api.start_app_update_check()`.

- [ ] **Step 4: Implement the UI**

`host/desktop/ui/index.html`:
- In each of `home:not_installed`, `home:stopped`, `home:running`, `home:wrong`, directly after the hero `</div>`, add:

```html
    <p class="update-note" data-when="app_update">Eggie <span data-field="app_update"></span> is available.
      <button class="btn-secondary" data-action="app-update">Update</button></p>
```

- Replace the `updates-unavailable` template with:

```html
<template data-screen="updates">
  <section class="pad centered">
    <div data-when="available">
      <h2 class="title">Eggie <span data-field="available"></span> is ready</h2>
      <p class="lede">Updating closes this window, installs the new version and opens it again.</p>
      <div class="row"><button class="btn-primary" data-action="app-update">Update</button>
        <button class="btn-secondary" data-action="go-home">Not now</button></div>
    </div>
    <div data-when="none">
      <h2 class="title">You're up to date</h2>
      <p class="lede">What runs inside the kitchen updates itself whenever the machine starts.</p>
      <div class="row"><button class="btn-secondary" data-action="go-home">Done</button></div>
    </div>
    <p class="hint">Version <span data-field="app_version"></span></p>
  </section>
</template>

<template data-screen="app-update:running">
  <section class="pad centered">
    <h2 class="title">Downloading Eggie <span data-field="available"></span></h2>
    <div class="bar"><div class="bar-fill" data-field="fraction" style="width:0%"></div></div>
    <p class="hint">The window closes when the installer starts.</p>
  </section>
</template>
```

(Check `index.html`'s `import:progress` template for the progress-bar class names actually used and copy them exactly instead of `bar`/`bar-fill` if they differ; add a `.update-note` rule to the stylesheet next to `.tile-note` matching its spacing.)

`host/desktop/ui/app.js` — replace the `check-updates` action and add:

```js
ACTIONS['check-updates'] = async () => {
  const result = await api().check_app_update();
  show('updates', Object.assign({ none: result.available ? '' : 'yes' }, result));
};

ACTIONS['app-update'] = async () => {
  const home = await api().home();
  const started = await api().start_app_update();
  if (started.ok === false) return refresh();
  show('app-update:running', { available: home.app_update });
};

window.eggie.handlers.app_update = (event) => {
  if (event.type === 'progress') {
    const bar = document.querySelector('[data-field="fraction"]');
    if (bar && event.total) bar.style.width = `${Math.round((event.done / event.total) * 100)}%`;
    return;
  }
  // 'done' needs nothing: the window is closing.
  if (event.type === 'crashed') { showNotice(event.message); refresh(); }
};
```

- [ ] **Step 5: Run tests**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest tests/host -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add host/desktop tests/host/desktop
git commit -m "Update button downloads, verifies and launches the new installer"
```

---

### Task 11: Docs

**Files:**
- Rewrite: `docs/releasing.md`
- Modify: `runtime/install/CLAUDE.md`, `CLAUDE.md` (seam list), `runtime/CLAUDE.md` (the "moving to a new release" lines), `docs/release-testing.md`, `docs/README.md`, `docs/superpowers/specs/2026-09-28-update-system-design.md`
- Delete: `docs/future/engine-self-update.md`

- [ ] **Step 1: Rewrite `docs/releasing.md`** — short and direct:

````markdown
# Releasing

The host (desktop app) and the runtime (everything inside the VM) release separately.

## How installed machines update

- **Runtime:** every VM checks at boot (`eggie-update.service`) and moves to the newest
  `runtime-vX.Y.Z` whose `runtime/release.json` `api` the host accepts (`/opt/eggie/host.json`).
  A host that finds an older API updates the runtime at once. A failed update keeps the old one.
- **Desktop app:** the app checks `host-v*` releases on launch and shows **Update**.

`runtime/install/get.sh` on `main` is what every host and every VM runs. Keep its env vars
(`EGGIE_RUNTIME_REPO`, `_REF`, `_REPAIR`, `_API`, `_UPDATE`), the marker
`/opt/eggie/runtime.version` and its exit codes backward compatible.

## Cut a runtime release

1. Bump the version in all five places (`tests/test_constants_agree.py` checks):
   `runtime/eggie_api/__init__.py`, `runtime/eggie_api/pyproject.toml`,
   `runtime/eggie_api/Dockerfile` (`SERVICE_VERSION`), `runtime/eggie_api/Dockerfile.debug`
   (`SERVICE_IMAGE`), `runtime/stack.yml` (api and web tags).
2. `packaging/images/build.sh --push` (amd64 + arm64; register qemu first).
3. `git tag runtime-vX.Y.Z && git push origin runtime-vX.Y.Z`

VMs pick it up at their next boot.

### Changing the API number

Only when a route the host calls changes incompatibly:

1. Bump `API_VERSION` in `runtime/eggie_api/core/constants.py` and `runtime/release.json`.
2. Release a host whose `SUPPORTED_API` includes the new number **before** tagging the runtime,
   or no VM will install it.

`SERVICE_VERSION` (the release number) and `API_VERSION` (the wire protocol) are different
numbers. Never merge them.

## Cut a host release

1. Bump `version` in `pyproject.toml` and `APP_VERSION` in `host/core/constants.py`.
2. Build on each platform (`docs/building.md`): `EggieSetup-X.Y.Z.exe`,
   `EggieSetup-X.Y.Z-arm64.pkg`, `EggieSetup-X.Y.Z-x86_64.pkg`.
3. Put all three in one folder and run `sha256sum EggieSetup-* > SHA256SUMS`.
4. `gh release create host-vX.Y.Z EggieSetup-* SHA256SUMS --title "Eggie X.Y.Z"`
   (not `--prerelease`, or no app will offer it).

## First-release checklist

- The repository is public, `runtime/install/get.sh` is on `main`.
- A `runtime-v*` tag with `runtime/release.json` exists.
- ghcr `eggie-api` and `eggie-web` are public and multi-arch.
- `github.com/eggie-io/eggie-skills` is public.

## Pin or repair a VM by hand

```powershell
wsl -d eggie-vm -u root -- bash -lc "curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/main/runtime/install/get.sh | EGGIE_RUNTIME_REF=runtime-vX.Y.Z bash"
```

```bash
limactl shell eggie-vm -- sudo bash -lc "curl -fsSL https://raw.githubusercontent.com/eggie-io/eggie/main/runtime/install/get.sh | EGGIE_RUNTIME_REF=runtime-vX.Y.Z bash"
```

Use `EGGIE_RUNTIME_REPAIR=1` instead to reinstall the current release. A VM on a pinned branch
(not a `runtime-v*` tag) is never moved by the boot update.
````

- [ ] **Step 2: Update the other docs**

- `runtime/install/CLAUDE.md`: in the `get.sh` section list the new env vars, the `resolve_ref` API filter, update mode (no-op / stage / swap / roll back), `runtime.env`, `update.lock`; add `lib/boot-update.sh` + `systemd/eggie-update.service` (enabled, never started by `install.sh`); add `tests/runtime/test_boot_update.py` to the tests list.
- Root `CLAUDE.md` seam sentence: add `/opt/eggie/host.json` (host → VM), `runtime/release.json`'s `api`.
- `runtime/CLAUDE.md`: replace "Re-running setup does nothing … run `get.sh` in the VM without repair" with "The VM updates itself at boot; see `docs/releasing.md`."
- `docs/release-testing.md`: add Windows and macOS rows — "Update from the previous host release via the Update button" (pass: installer runs, app reopens on Windows, new version in the footer); "Boot a VM with a newer compatible runtime tag published" (pass: `runtime.version` moves, projects still run); "Boot with the network off" (pass: VM starts on its old runtime, `journalctl -u eggie-update` explains).
- `docs/README.md`: remove the `future/` row; change the `releasing.md` row to "Cutting runtime and host releases, and how installed machines update".
- `git rm docs/future/engine-self-update.md`.
- Spec: under section 4 "Trigger" add "An installed ref that is not a `runtime-vN.N.N` tag (a pinned branch) is left alone." Under section 3 item 3 add "The host's stub exports `EGGIE_RUNTIME_URL` to `get.sh`; without it (`curl | bash` on a cloud VM) `get.sh` records `$REPO/raw/main/runtime/install/get.sh`." Set `Status: implemented`.

- [ ] **Step 3: Run the full suite**

Run: `TMPDIR=$PWD/.tmp python3 -m pytest -q`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add -A docs CLAUDE.md runtime/CLAUDE.md runtime/install/CLAUDE.md
git commit -m "docs: releasing and updating, short and direct"
```
