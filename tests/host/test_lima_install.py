"""Putting a pinned Lima on disk.

Downloading is the easy half. The half that matters is refusing to believe the
work happened: this repo has already shipped two provisioning steps that
reported success for work that never ran, so `install()` ends by running the
binary and reading its version back.
"""
import io
import tarfile
from pathlib import Path

import pytest

from host.core.images import Image
from host.providers import lima_install


class FakeRunner:
    """Same shape as the providers' runners: returns an object with
    returncode/stdout/stderr, and never raises."""

    def __init__(self, stdout=b"limactl version 2.2.0\n", returncode=0):
        self.calls = []
        self._out, self._rc = stdout, returncode

    def __call__(self, argv):
        self.calls.append(argv)
        class R:
            returncode = self._rc
            stdout = self._out
            stderr = b""
        return R()


def _tarball(dest: Path, members: dict[str, bytes], *, mode=0o755) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            tar.addfile(info, io.BytesIO(payload))
    return dest


def _good_tarball(dest: Path) -> Path:
    return _tarball(dest, {"bin/limactl": b"#!/bin/sh\n",
                           "share/lima/lima-guestagent.Linux-aarch64": b"x"})


def _fetcher(payload_for):
    """Stands in for download.fetch: writes bytes where it was told to, and
    records that it was asked. The real one is digest-checked and resumable;
    neither behaviour belongs in these tests."""
    calls = []

    def fetch(image, dest, *, on_progress=None):
        calls.append((image, Path(dest)))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        payload_for(Path(dest))
        if on_progress:
            on_progress(10, 10)
        return Path(dest)

    fetch.calls = calls
    return fetch


# --- which archive ---

@pytest.mark.parametrize("machine,key", [
    ("arm64", "arm64"), ("aarch64", "arm64"), ("ARM64", "arm64"),
    ("x86_64", "x86_64"), ("amd64", "x86_64"), ("AMD64", "x86_64"),
])
def test_archive_for_normalizes_the_machine_name(machine, key):
    assert lima_install.archive_for(machine) is lima_install.ARCHIVES[key]


def test_archive_for_refuses_an_architecture_we_have_no_build_for():
    with pytest.raises(lima_install.LimaInstallError) as caught:
        lima_install.archive_for("ppc64le")
    assert "ppc64le" in str(caught.value)


def test_both_architectures_are_pinned_with_a_real_digest():
    # The digests come from the release's SHA256SUMS. A truncated or
    # placeholder value here disables `fetch`'s only integrity check.
    assert set(lima_install.ARCHIVES) == {"arm64", "x86_64"}
    for key, image in lima_install.ARCHIVES.items():
        assert lima_install.LIMA_VERSION in image.url
        assert key in image.url
        assert len(image.sha256) == 64
        assert set(image.sha256) <= set("0123456789abcdef")


# --- installing ---

def test_install_unpacks_the_archive_and_returns_the_binary(tmp_path):
    fetch = _fetcher(_good_tarball)
    runner = FakeRunner()

    path = lima_install.install(tmp_path, fetch=fetch, runner=runner)

    assert path == tmp_path / "lima" / "bin" / "limactl"
    assert path.is_file()
    assert (tmp_path / "lima" / "share" / "lima").is_dir()
    # The share directory has to stay a sibling of bin/: limactl finds its own
    # templates and guest agents relative to the executable.
    assert path.parent.parent == tmp_path / "lima"


def test_install_verifies_the_binary_before_moving_it_into_place(tmp_path):
    runner = FakeRunner()
    lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=runner)
    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert argv[1] == "--version"
    assert argv[0].endswith("bin/limactl")
    assert argv[0] != str(tmp_path / "lima" / "bin" / "limactl"), (
        "the binary must be verified in its staging directory: a swap-then-verify "
        "leaves a broken limactl in place if the process dies mid-check")


def test_a_bad_download_never_disturbs_a_working_install(tmp_path):
    lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=FakeRunner())
    final = tmp_path / "lima" / "bin" / "limactl"
    final.write_text("#!/bin/sh\n# the good one\n")
    (tmp_path / "lima" / ".version").write_text("1.0.0")

    seen = {}

    class Watching(FakeRunner):
        def __call__(self, argv):
            seen["intact"] = "# the good one" in final.read_text()
            return super().__call__(argv)

    with pytest.raises(lima_install.LimaInstallError):
        lima_install.install(tmp_path, fetch=_fetcher(_good_tarball),
                             runner=Watching(stdout=b"limactl version 1.0.0\n"))
    assert seen["intact"], "the working install was disturbed before the new one was verified"
    assert "# the good one" in final.read_text()


def test_install_fails_when_the_unpacked_binary_reports_another_version(tmp_path):
    runner = FakeRunner(stdout=b"limactl version 1.0.0\n")
    with pytest.raises(lima_install.LimaInstallError) as caught:
        lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=runner)
    assert "1.0.0" in str(caught.value)
    assert lima_install.LIMA_VERSION in str(caught.value)


def test_install_fails_when_the_binary_will_not_run(tmp_path):
    runner = FakeRunner(stdout=b"", returncode=126)
    with pytest.raises(lima_install.LimaInstallError):
        lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=runner)


def test_install_fails_when_the_archive_has_no_limactl(tmp_path):
    fetch = _fetcher(lambda dest: _tarball(dest, {"share/lima/templates": b"x"}))
    with pytest.raises(lima_install.LimaInstallError) as caught:
        lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    assert "bin/limactl" in str(caught.value)


def test_install_is_skipped_when_the_pinned_version_is_already_there(tmp_path):
    fetch = _fetcher(_good_tarball)
    first = lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    second = lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    assert first == second
    assert len(fetch.calls) == 1, "a re-run must not download Lima again"


def test_install_replaces_a_different_version_that_is_already_there(tmp_path):
    lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=FakeRunner())
    (tmp_path / "lima" / ".version").write_text("1.0.0")
    fetch = _fetcher(_good_tarball)
    lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    assert len(fetch.calls) == 1
    assert (tmp_path / "lima" / ".version").read_text().strip() == lima_install.LIMA_VERSION


def test_install_reports_download_progress(tmp_path):
    seen = []
    lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=FakeRunner(),
                         on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(10, 10)]


# --- extraction safety ---

def test_extraction_refuses_an_absolute_member(tmp_path):
    # tarfile's data filter normalizes an absolute name instead of refusing it,
    # which is why this check is explicit -- the same finding as
    # eggie_api/infra/files.extract_archive.
    fetch = _fetcher(lambda dest: _tarball(dest, {"/etc/passwd": b"pwned",
                                                  "bin/limactl": b"#!/bin/sh\n"}))
    with pytest.raises(lima_install.LimaInstallError):
        lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    assert not (tmp_path / "lima").exists()


def test_extraction_refuses_a_parent_escape(tmp_path):
    fetch = _fetcher(lambda dest: _tarball(dest, {"../escaped": b"pwned",
                                                  "bin/limactl": b"#!/bin/sh\n"}))
    with pytest.raises(Exception):
        lima_install.install(tmp_path, fetch=fetch, runner=FakeRunner())
    assert not (tmp_path.parent / "escaped").exists()


def test_a_failed_install_leaves_the_previous_one_in_place(tmp_path):
    lima_install.install(tmp_path, fetch=_fetcher(_good_tarball), runner=FakeRunner())
    (tmp_path / "lima" / ".version").write_text("1.0.0")
    broken = _fetcher(lambda dest: _tarball(dest, {"share/lima/x": b"x"}))
    with pytest.raises(lima_install.LimaInstallError):
        lima_install.install(tmp_path, fetch=broken, runner=FakeRunner())
    assert (tmp_path / "lima" / "bin" / "limactl").is_file()
