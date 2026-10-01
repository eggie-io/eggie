"""Which image tag a runtime ref runs: the one number a release carries."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "image-version.sh"


def _run(tmp_path, ref, *, tags=(), git_code=0, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    listing = tmp_path / "tags.txt"
    listing.write_text("".join(f"{'0' * 40}\trefs/tags/{t}\n" for t in tags))
    git = bin_dir / "git"
    git.write_text(f"#!/bin/sh\ncat '{listing}'\nexit {git_code}\n")
    git.chmod(0o755)
    environ = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    environ.pop("OMELET_IMAGE_VERSION", None)
    environ.update(env)
    return subprocess.run(["bash", str(SCRIPT), ref, "https://example.invalid/repo"],
                          env=environ, capture_output=True, text=True)


def test_a_release_runs_its_own_images(tmp_path):
    result = _run(tmp_path, "runtime-v0.4.0", tags=["runtime-v0.9.0"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.4.0"


def test_a_branch_runs_the_newest_releases_images_by_version_not_text(tmp_path):
    result = _run(tmp_path, "feature/x",
                  tags=["runtime-v0.9.0", "runtime-v0.10.0", "runtime-v1.0.0-rc1"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.10.0"


def test_a_branch_can_name_the_images_it_runs(tmp_path):
    result = _run(tmp_path, "feature/x", tags=["runtime-v0.9.0"], OMELET_IMAGE_VERSION="dev")
    assert result.stdout.strip() == "dev"


def test_a_branch_with_no_release_to_borrow_from_is_a_plain_failure(tmp_path):
    result = _run(tmp_path, "feature/x", tags=[])
    assert result.returncode != 0
    assert "no runtime-v" in result.stderr


def test_an_unreachable_repository_is_a_plain_failure(tmp_path):
    result = _run(tmp_path, "feature/x", git_code=128)
    assert result.returncode != 0
    assert result.stdout.strip() == ""
