# Default Shell Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user opening a shell in the VM (`wsl -d omelet-vm`, `ssh`, `limactl shell`) lands in `~/projects` (→ `/opt/omelet/projects`) without picking a directory (omelet-app/omelet-resources#43).

**Architecture:** The runtime installs a POSIX `sh` snippet to `/etc/profile.d/omelet-cwd.sh`; every login shell sources it, and it `cd`s to `~/projects` only when the shell is interactive and started in `$HOME`. WSL starts in the Windows caller's folder (`/mnt/c/...`), not `$HOME`, so the host's WSL access command adds `--cd ~`. The host never names the guest path — the seam rule in `CLAUDE.md`.

**Tech Stack:** bash/POSIX sh, Python 3.12, pytest.

**Spec:** this plan (design agreed in conversation; issue body: "Ideally, if they can open WSL and that's all").

## Global Constraints

- `profile.d` scripts are sourced by `/etc/profile` under dash as well as bash: POSIX `sh` only — no `[[ ]]`, no bash-isms.
- Never `cd` in a non-interactive shell: coding agents, `scp`, VS Code Remote-SSH's server and `ssh host cmd` must keep their cwd.
- No `RemoteCommand` in any ssh config — VS Code Remote-SSH refuses hosts that set it.
- Host must not reference `/opt/omelet/projects` in the WSL command (host/runtime seam; `--cd` to a missing dir also makes `wsl.exe` fail before the runtime is installed).
- Tests reach repo files via `__file__`, never cwd-relative paths.
- Run tests with `TMPDIR=<writable dir> python3 -m pytest -q` (python3 is 3.12 here).

## Review Focus

- Shell opened somewhere other than `$HOME` (WSL launched from a Windows folder without `--cd`, `cd` already chosen by a tool) → stays where it is. Pinned in Task 1.
- Non-interactive shell (agent / scp / `ssh host cmd`) → cwd untouched. Pinned in Task 1.
- Account with no `~/projects` (a real folder was left alone, or a user created after install) → shell still starts, stays in `$HOME`. Pinned in Task 1.
- Sourced by dash (`/bin/sh` login) as well as bash → works in both. Pinned in Task 1 (parametrized over `sh` and `bash`).
- `install.sh` copies from a path that does not exist → install fails loudly vs. silently no snippet. Pinned in Task 1 (source path resolved and checked).

---

### Task 1: Runtime profile snippet, installed by install.sh

**Files:**
- Create: `runtime/install/profile/omelet-cwd.sh`
- Modify: `runtime/install/install.sh` (step 9, after the `/etc/claude-code/CLAUDE.md` line)
- Modify: `runtime/install/CLAUDE.md` (step list: mention `/etc/profile.d/omelet-cwd.sh` after `/etc/claude-code/CLAUDE.md`)
- Create: `tests/runtime/test_profile_cwd.py`
- Modify: `tests/runtime/test_install_shell.py` (append one test)

**Interfaces:**
- Produces: guest file `/etc/profile.d/omelet-cwd.sh`; relies on the existing `~/projects` link from `lib/install-agents.sh`.

- [ ] **Step 1: Write the failing tests**

`tests/runtime/test_profile_cwd.py`:

```python
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "profile" / "omelet-cwd.sh"

# /etc/profile sources profile.d under dash too, when an account's shell is sh.
SHELLS = ["sh", "bash"]


def _start(shell: str, home: Path, cwd: Path, interactive: bool) -> str:
    flags = "-ic" if interactive else "-c"
    # No inherited PWD: the shell must derive it from the real cwd, as a login does.
    env = {"HOME": str(home), "PATH": os.environ["PATH"]}
    result = subprocess.run([shell, flags, f'. "{SCRIPT}"; pwd'], cwd=cwd, env=env,
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "opt-projects"
    target.mkdir()
    (home / "projects").symlink_to(target)
    return home


@pytest.mark.parametrize("shell", SHELLS)
def test_an_interactive_shell_in_home_opens_in_projects(shell, home):
    assert _start(shell, home, home, interactive=True) == str(home / "projects")


@pytest.mark.parametrize("shell", SHELLS)
def test_a_non_interactive_shell_keeps_its_directory(shell, home):
    # Agents, scp and VS Code's server run commands this way.
    assert _start(shell, home, home, interactive=False) == str(home)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_shell_started_elsewhere_stays_there(shell, home, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _start(shell, home, elsewhere, interactive=True) == str(elsewhere)


@pytest.mark.parametrize("shell", SHELLS)
def test_an_account_without_projects_stays_in_home(shell, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _start(shell, bare, bare, interactive=True) == str(bare)
```

Append to `tests/runtime/test_install_shell.py`:

```python
def test_install_ships_the_shell_start_directory_snippet():
    # A wrong source path here installs nothing, and shells quietly open in $HOME.
    match = re.search(r'install -m 644 "\$INSTALL_DIR/(\S+)" /etc/profile\.d/omelet-cwd\.sh',
                      INSTALL.read_text())
    assert match, "install.sh does not install /etc/profile.d/omelet-cwd.sh"
    assert (INSTALL.parent / match.group(1)).is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `TMPDIR=$SCRATCH python3 -m pytest -q tests/runtime/test_profile_cwd.py tests/runtime/test_install_shell.py`
Expected: profile tests FAIL (`. ".../omelet-cwd.sh"`: file not found → non-zero exit); install test FAILs on the missing `install` line.

- [ ] **Step 3: Implement**

`runtime/install/profile/omelet-cwd.sh`:

```sh
# Sourced by /etc/profile, under dash as well as bash: POSIX sh only.
# Only interactive shells that started in $HOME move; agents, scp and
# VS Code's server run non-interactive and keep their own directory.
case $- in
  *i*)
    if [ "$PWD" = "$HOME" ] && [ -d "$HOME/projects" ]; then
      cd "$HOME/projects" || true
    fi
    ;;
esac
```

`runtime/install/install.sh`, step 9 — after `install -m 644 "$RUNTIME_DIR/instructions/omelet.md" /etc/claude-code/CLAUDE.md`, add:

```bash
# A login shell opens in ~/projects instead of an empty home.
install -m 644 "$INSTALL_DIR/profile/omelet-cwd.sh" /etc/profile.d/omelet-cwd.sh
```

And rename the step heading to `# 9. the in-VM omelet command, the instructions every session loads, and the shell's start directory.`

`runtime/install/CLAUDE.md`: in the `install.sh` step list, change
`` `/usr/local/bin/omelet` → `/etc/claude-code/CLAUDE.md` → `` to
`` `/usr/local/bin/omelet` → `/etc/claude-code/CLAUDE.md` → `/etc/profile.d/omelet-cwd.sh` (interactive login shells in `$HOME` open in `~/projects`) → ``.

- [ ] **Step 4: Run tests to verify they pass**

Run: `TMPDIR=$SCRATCH python3 -m pytest -q tests/runtime/`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/install/profile/omelet-cwd.sh runtime/install/install.sh runtime/install/CLAUDE.md \
  tests/runtime/test_profile_cwd.py tests/runtime/test_install_shell.py
git commit -m "feat(runtime): open interactive VM shells in ~/projects"
```

### Task 2: WSL access command starts in the home directory

**Files:**
- Modify: `host/providers/wsl2.py` (`access()`, ~line 377)
- Modify: `tests/host/test_access.py` (`test_wsl2_does_not_pretend_to_have_an_ssh_server`)
- Modify: `docs/vm.md` (one sentence after the table)

**Interfaces:**
- Consumes: Task 1's snippet in the guest (runtime side; no code dependency).

- [ ] **Step 1: Update the test**

In `tests/host/test_access.py`, replace `assert access.command == "wsl -d omelet-vm"` with:

```python
    # --cd ~: wsl.exe otherwise opens in the caller's Windows folder, which the
    # guest's profile snippet leaves alone; from $HOME it moves to ~/projects.
    assert access.command == "wsl -d omelet-vm --cd ~"
```

- [ ] **Step 2: Run to verify it fails**

Run: `TMPDIR=$SCRATCH python3 -m pytest -q tests/host/test_access.py`
Expected: FAIL, `'wsl -d omelet-vm' == 'wsl -d omelet-vm --cd ~'`.

- [ ] **Step 3: Implement**

In `host/providers/wsl2.py` `access()`:

```python
            command=f"wsl -d {self.distro} --cd ~",
```

In `docs/vm.md`, after the "On Windows, always pass `-d omelet-vm`..." paragraph, add:

```markdown
An interactive login shell that starts in `$HOME` moves to `~/projects` (`/etc/profile.d/omelet-cwd.sh`,
installed by the runtime). On Windows add `--cd ~` — without it `wsl` opens in the current Windows folder
and stays there.
```

- [ ] **Step 4: Run full suite**

Run: `TMPDIR=$SCRATCH python3 -m pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add host/providers/wsl2.py tests/host/test_access.py docs/vm.md
git commit -m "feat(host): start the WSL shell in the guest's home directory"
```
