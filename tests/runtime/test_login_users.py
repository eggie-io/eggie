"""login-users.sh selects the accounts install.sh should provision Eggie's
agent files into: real login accounts, on both WSL2 (uid 1000) and Lima, whose
guest user carries the macOS host uid (often 501, always < 1000). Lowering the
old uid>=1000 bound alone would sweep in system accounts whose home is `/`, so
this script's job is exactly the extra filtering that makes a lower bound safe.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "login-users.sh"


def _run(passwd_lines: list[str], shells: list[str], tmp_path: Path) -> list[str]:
    shells_file = tmp_path / "shells"
    shells_file.write_text("\n".join(shells) + "\n")
    passwd = "\n".join(passwd_lines) + "\n"
    result = subprocess.run(["bash", str(SCRIPT), str(shells_file)],
                            input=passwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def _home(tmp_path: Path, name: str) -> Path:
    home = tmp_path / "home" / name
    home.mkdir(parents=True)
    return home


def test_login_users_is_valid_bash():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_a_uid_501_bash_user_is_selected(tmp_path):
    # Lima's login account carries the macOS host uid, often 501.
    home = _home(tmp_path, "lima")
    line = f"lima:x:501:501:Lima:{home}:/bin/bash"
    assert _run([line], ["/bin/bash"], tmp_path) == [f"lima:501:501:{home}"]


def test_a_uid_1000_user_is_selected(tmp_path):
    home = _home(tmp_path, "ubuntu")
    line = f"ubuntu:x:1000:1000:Ubuntu:{home}:/bin/bash"
    assert _run([line], ["/bin/bash"], tmp_path) == [f"ubuntu:1000:1000:{home}"]


def test_a_nologin_shell_is_not_selected(tmp_path):
    home = _home(tmp_path, "svc")
    line = f"svc:x:998:998:Service:{home}:/usr/sbin/nologin"
    assert _run([line], ["/usr/sbin/nologin"], tmp_path) == []


def test_a_false_shell_is_not_selected(tmp_path):
    home = _home(tmp_path, "svc2")
    line = f"svc2:x:998:998:Service:{home}:/bin/false"
    assert _run([line], ["/bin/false"], tmp_path) == []


def test_an_account_whose_home_is_root_is_not_selected(tmp_path):
    line = "daemon:x:501:501:Daemon:/:/bin/bash"
    assert _run([line], ["/bin/bash"], tmp_path) == []


def test_nobody_is_not_selected(tmp_path):
    home = _home(tmp_path, "nobody")
    line = f"nobody:x:65534:65534:nobody:{home}:/usr/sbin/nologin"
    assert _run([line], ["/usr/sbin/nologin"], tmp_path) == []


def test_a_real_shell_whose_home_does_not_exist_is_not_selected(tmp_path):
    missing = tmp_path / "home" / "ghost"
    line = f"ghost:x:1500:1500:Ghost:{missing}:/bin/bash"
    assert _run([line], ["/bin/bash"], tmp_path) == []


def test_a_shell_not_listed_in_the_shells_file_is_not_selected(tmp_path):
    home = _home(tmp_path, "weird")
    line = f"weird:x:1500:1500:Weird:{home}:/usr/local/bin/fish"
    assert _run([line], ["/bin/bash"], tmp_path) == []
