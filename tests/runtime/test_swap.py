"""swap.sh gives a VM without swap a swapfile, once. Runs under real bash with
fake `swapon`, `df`, `fallocate` and `mkswap` on PATH."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "swap.sh"

FAKE_SWAPON = """#!/usr/bin/env bash
if [[ "$1" == --show ]]; then cat "$STATE/active" 2>/dev/null; exit 0; fi
echo "$1" > "$STATE/active"
"""
FAKE_DF = """#!/usr/bin/env bash
printf 'Avail\\n%sG\\n' "$FREE_GB"
"""
FAKE_FALLOCATE = """#!/usr/bin/env bash
touch "${@: -1}"
"""
FAKE_MKSWAP = """#!/usr/bin/env bash
exit "${MKSWAP_EXIT:-0}"
"""


def _setup(tmp_path, *, free_gb=50, active=None, **extra):
    bin_dir, state = tmp_path / "bin", tmp_path / "state"
    bin_dir.mkdir(), state.mkdir()
    for name, text in {"swapon": FAKE_SWAPON, "df": FAKE_DF,
                       "fallocate": FAKE_FALLOCATE, "mkswap": FAKE_MKSWAP}.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    if active:
        (state / "active").write_text(active)
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "STATE": str(state),
           "FREE_GB": str(free_gb), **extra}
    return env, tmp_path / "swapfile", tmp_path / "fstab"


def _run(env, swapfile, fstab):
    return subprocess.run(["bash", str(SCRIPT), str(swapfile), str(fstab)],
                          env=env, capture_output=True, text=True)


def test_a_vm_without_swap_gets_a_swapfile_that_survives_reboot(tmp_path):
    env, swapfile, fstab = _setup(tmp_path)
    assert _run(env, swapfile, fstab).returncode == 0
    assert swapfile.exists()
    assert (tmp_path / "state" / "active").read_text().strip() == str(swapfile)
    assert fstab.read_text() == f"{swapfile} none swap sw 0 0\n"


def test_rerunning_adds_the_fstab_entry_once(tmp_path):
    env, swapfile, fstab = _setup(tmp_path)
    _run(env, swapfile, fstab)
    (tmp_path / "state" / "active").unlink()    # as after a swapoff
    assert _run(env, swapfile, fstab).returncode == 0
    assert fstab.read_text().count(str(swapfile)) == 1


def test_existing_swap_such_as_wsls_is_left_alone(tmp_path):
    env, swapfile, fstab = _setup(tmp_path, active="/swap/file 4G\n")
    assert _run(env, swapfile, fstab).returncode == 0
    assert not swapfile.exists() and not fstab.exists()


def test_a_nearly_full_disk_gets_no_swap(tmp_path):
    env, swapfile, fstab = _setup(tmp_path, free_gb=12)
    result = _run(env, swapfile, fstab)
    assert result.returncode == 0
    assert not swapfile.exists() and "not adding swap" in result.stderr


def test_a_failed_mkswap_leaves_no_half_made_swapfile(tmp_path):
    env, swapfile, fstab = _setup(tmp_path, MKSWAP_EXIT="1")
    assert _run(env, swapfile, fstab).returncode != 0
    assert list(tmp_path.glob("swapfile*")) == []
