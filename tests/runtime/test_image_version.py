"""Which image tag a runtime ref runs: its own number for a release, its own
name when ghcr holds images pushed under it, the newest release's otherwise."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "runtime" / "install" / "lib" / "image-version.sh"


def _fake_curl(tmp_path, images, ghcr_code):
    # images: "name:tag" pairs ghcr holds. ghcr_code: what every manifest
    # probe answers instead, with curl's own failure exit.
    listing = tmp_path / "images.txt"
    listing.write_text("".join(f"{i}\n" for i in images))
    probe = (f"echo {ghcr_code}; exit 7" if ghcr_code else
             f"if grep -qx \"$name:$tag\" '{listing}'; then echo 200; else echo 404; fi")
    return ("#!/bin/sh\n"
            f"echo \"$*\" >> '{tmp_path / 'curl.log'}'\n"
            'case "$*" in\n'
            "  *'ghcr.io/token?'*) echo '{\"token\":\"fake\"}' ;;\n"
            "  *'/manifests/'*)\n"
            '    for a; do case "$a" in https://*) url=$a ;; esac; done\n'
            '    tag=${url##*/manifests/}\n'
            '    name=${url#https://ghcr.io/v2/eggie-io/}; name=${name%%/*}\n'
            f"    {probe} ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n")


def _run(tmp_path, ref, *, tags=(), git_code=0, images=(), ghcr_code=None, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    listing = tmp_path / "tags.txt"
    listing.write_text("".join(f"{'0' * 40}\trefs/tags/{t}\n" for t in tags))
    git = bin_dir / "git"
    git.write_text(f"#!/bin/sh\ncat '{listing}'\nexit {git_code}\n")
    git.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(_fake_curl(tmp_path, images, ghcr_code))
    curl.chmod(0o755)
    environ = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    environ.pop("EGGIE_IMAGE_VERSION", None)
    environ.update(env)
    return subprocess.run(["bash", str(SCRIPT), ref, "https://example.invalid/repo"],
                          env=environ, capture_output=True, text=True)


def _ghcr_calls(tmp_path):
    log = tmp_path / "curl.log"
    return log.read_text().splitlines() if log.exists() else []


def test_a_release_runs_its_own_images_without_asking_anyone(tmp_path):
    result = _run(tmp_path, "runtime-v0.4.0", tags=["runtime-v0.9.0"],
                  images=["eggie-api:runtime-v0.4.0", "eggie-web:runtime-v0.4.0"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.4.0"
    assert _ghcr_calls(tmp_path) == []


def test_a_branch_with_both_images_under_its_name_runs_them(tmp_path):
    # git fails on purpose: the repo's releases are never needed then.
    result = _run(tmp_path, "dev", git_code=128,
                  images=["eggie-api:dev", "eggie-web:dev"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "dev"


def test_a_branch_with_only_one_image_under_its_name_runs_the_newest_release(tmp_path):
    result = _run(tmp_path, "dev", tags=["runtime-v0.9.0"], images=["eggie-web:dev"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.9.0"


def test_a_branch_runs_the_newest_releases_images_by_version_not_text(tmp_path):
    result = _run(tmp_path, "feature/x",
                  tags=["runtime-v0.9.0", "runtime-v0.10.0", "runtime-v1.0.0-rc1"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.10.0"


def test_a_ref_no_image_tag_can_be_named_after_is_not_looked_up(tmp_path):
    result = _run(tmp_path, "feature/x", tags=["runtime-v0.9.0"])
    assert result.returncode == 0, result.stderr
    assert _ghcr_calls(tmp_path) == []


def test_naming_the_images_beats_the_branchs_own(tmp_path):
    result = _run(tmp_path, "dev", images=["eggie-api:dev", "eggie-web:dev"],
                  EGGIE_IMAGE_VERSION="0.3.0")
    assert result.stdout.strip() == "0.3.0"
    assert _ghcr_calls(tmp_path) == []


def test_a_branch_with_no_release_to_borrow_from_is_a_plain_failure(tmp_path):
    result = _run(tmp_path, "dev", tags=[])
    assert result.returncode != 0
    assert "no runtime-v" in result.stderr


def test_an_unreachable_repository_is_a_plain_failure(tmp_path):
    result = _run(tmp_path, "dev", git_code=128)
    assert result.returncode != 0
    assert result.stdout.strip() == ""


def test_an_unreachable_ghcr_is_a_plain_failure_not_a_silent_release(tmp_path):
    result = _run(tmp_path, "dev", tags=["runtime-v0.9.0"], ghcr_code="000")
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert "ghcr.io" in result.stderr
