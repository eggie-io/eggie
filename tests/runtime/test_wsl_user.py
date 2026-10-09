"""wsl-user.sh gives a WSL VM the non-root login account `wsl -d` opens as.
Runs under real bash with fake `id`, `useradd` and `visudo` on PATH."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "wsl-user.sh"

FAKE_ID = """#!/usr/bin/env bash
[[ -e "$STATE/exists" ]] && echo 1001 || exit 1
"""
FAKE_USERADD = """#!/usr/bin/env bash
echo "$*" >> "$STATE/useradd"
touch "$STATE/exists"
"""
FAKE_VISUDO = """#!/usr/bin/env bash
exit "${VISUDO_EXIT:-0}"
"""


def _setup(tmp_path, *, exists=False, conf="[boot]\nsystemd=true\n"):
    bin_dir, state, sudoers = (tmp_path / "bin", tmp_path / "state",
                               tmp_path / "sudoers.d")
    for d in (bin_dir, state, sudoers):
        d.mkdir()
    for name, text in {"id": FAKE_ID, "useradd": FAKE_USERADD,
                       "visudo": FAKE_VISUDO}.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    if exists:
        (state / "exists").touch()
    wsl_conf = tmp_path / "wsl.conf"
    wsl_conf.write_text(conf)
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin",
           "STATE": str(state)}
    return env, wsl_conf, sudoers, state


def _run(env, wsl_conf, sudoers):
    return subprocess.run(["bash", str(SCRIPT), str(wsl_conf), str(sudoers)],
                          env=env, capture_output=True, text=True)


def test_the_new_account_never_gets_the_api_uid(tmp_path):
    env, wsl_conf, sudoers, state = _setup(tmp_path)
    assert _run(env, wsl_conf, sudoers).returncode == 0
    assert "-K UID_MIN=1001" in (state / "useradd").read_text()


def test_an_existing_account_is_not_recreated(tmp_path):
    env, wsl_conf, sudoers, state = _setup(tmp_path, exists=True)
    assert _run(env, wsl_conf, sudoers).returncode == 0
    assert not (state / "useradd").exists()


def test_rerunning_sets_the_default_user_once_and_keeps_systemd(tmp_path):
    env, wsl_conf, sudoers, _ = _setup(tmp_path)
    for _ in range(2):
        assert _run(env, wsl_conf, sudoers).returncode == 0
    text = wsl_conf.read_text()
    assert text.count("[user]") == 1
    assert "default=eggie" in text
    assert "systemd=true" in text


def test_a_default_user_already_chosen_is_left_alone(tmp_path):
    conf = "[boot]\nsystemd=true\n[user]\ndefault=ada\n"
    env, wsl_conf, sudoers, _ = _setup(tmp_path, conf=conf)
    assert _run(env, wsl_conf, sudoers).returncode == 0
    assert wsl_conf.read_text() == conf


def test_passwordless_sudo_is_granted_with_sudoers_mode(tmp_path):
    env, wsl_conf, sudoers, _ = _setup(tmp_path)
    assert _run(env, wsl_conf, sudoers).returncode == 0
    rule = sudoers / "eggie"
    assert rule.read_text() == "eggie ALL=(ALL) NOPASSWD:ALL\n"
    # sudo ignores a sudoers.d file that is group- or world-writable.
    assert rule.stat().st_mode & 0o777 == 0o440


def test_a_rule_visudo_rejects_is_never_installed(tmp_path):
    # A broken file in sudoers.d breaks sudo for every account.
    env, wsl_conf, sudoers, _ = _setup(tmp_path)
    env["VISUDO_EXIT"] = "1"
    assert _run(env, wsl_conf, sudoers).returncode != 0
    assert not (sudoers / "eggie").exists()
