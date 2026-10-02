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
    # Suppress bash.bashrc sudo hint in interactive shells on Ubuntu.
    if interactive:
        (home / ".sudo_as_admin_successful").touch()
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
