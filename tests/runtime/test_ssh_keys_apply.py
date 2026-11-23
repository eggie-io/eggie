import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "ssh-keys-apply.sh"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc ada@laptop"
PASSWD = "root:x:0:0::/root:/bin/bash\nada:x:1001:1001::/home/ada:/bin/bash\n"


def setup(tmp_path, desired):
    keys, target = tmp_path / "keys", tmp_path / "target"
    keys.mkdir()
    passwd = tmp_path / "passwd"
    passwd.write_text(PASSWD)
    if desired is not None:
        text = desired if isinstance(desired, str) else json.dumps(desired)
        (keys / "desired.json").write_text(text)
    env = {**os.environ, "EGGIE_PASSWD_FILE": str(passwd)}
    return keys, target, env


def run(keys, target, env):
    return _run(keys, target, env).returncode


def _run(keys, target, env):
    result = subprocess.run(["bash", str(SCRIPT), str(keys), str(target)],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result


def test_writes_the_login_file_with_only_valid_keys(tmp_path):
    lines = [KEY, 'command="rm -rf /" ssh-ed25519 AAAA x', "ssh-rsa AAAAB3Nza x",
             "ssh-ed25519 AAAA ok\nssh-ed25519 BBBB injected"]
    keys, target, env = setup(tmp_path, {"login": "ada", "keys": lines})
    run(keys, target, env)
    assert (target / "ada").read_text().splitlines() == [KEY]
    assert oct((target / "ada").stat().st_mode & 0o777) == "0o644"
    assert oct(target.stat().st_mode & 0o777) == "0o755"


def test_unknown_login_writes_nothing(tmp_path):
    # root exists in passwd but its home is not under /home.
    for i, login in enumerate(("ghost", "root", "../ada")):
        case = tmp_path / str(i)
        case.mkdir()
        keys, target, env = setup(case, {"login": login, "keys": [KEY]})
        run(keys, target, env)
        assert not target.exists() or list(target.iterdir()) == []


def test_other_logins_files_are_removed(tmp_path):
    keys, target, env = setup(tmp_path, {"login": "ada", "keys": [KEY]})
    target.mkdir()
    (target / "bob").write_text("old")
    run(keys, target, env)
    assert [p.name for p in target.iterdir()] == ["ada"]


def test_bad_json_keeps_the_current_file(tmp_path):
    keys, target, env = setup(tmp_path, "{not json")
    target.mkdir()
    (target / "ada").write_text("keep")
    run(keys, target, env)
    assert (target / "ada").read_text() == "keep"


def test_a_dropped_key_is_reported_by_index_without_its_text(tmp_path):
    keys, target, env = setup(tmp_path, {"login": "ada", "keys": [KEY, "ssh-rsa SECRETBODY x"]})
    result = _run(keys, target, env)
    assert "key 1 dropped" in result.stderr
    assert "SECRETBODY" not in result.stderr
