import base64
import os
import re
import subprocess

import pytest

from host.core import constants
from host.core.bootstrap import BootstrapError, bootstrap
from host.core.provider import Completed


class FakeProvider:
    """Guest stand-in. A successful installer run writes the marker, as
    runtime/install/install.sh does on its last line."""

    def __init__(self, *, installed=False, fail=False, stderr="", writes_marker=True):
        self.execs = []
        self.installed = installed
        self._fail = fail
        self._stderr = stderr
        self._writes_marker = writes_marker

    def exec(self, argv, *, root=False):
        self.execs.append((argv, root))
        if argv[:2] == ["test", "-s"]:
            return Completed(0 if self.installed else 1, "", "")
        if self._fail:
            return Completed(1, "", self._stderr)
        if self._writes_marker:
            self.installed = True
        return Completed(0, "", "")

    def installer_runs(self):
        return [(argv, root) for argv, root in self.execs if argv[:2] != ["test", "-s"]]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("OMELET_RUNTIME_URL", "OMELET_RUNTIME_REF"):
        monkeypatch.delenv(name, raising=False)


def _command(provider) -> str:
    (run,) = provider.installer_runs()
    argv, _root = run
    assert argv[:2] == ["bash", "-lc"]
    return argv[2]


def _stub(command: str) -> str:
    return base64.b64decode(re.search(r"echo (\S+) \| base64 -d", command)[1]).decode()


def test_an_installed_runtime_is_left_alone():
    p = FakeProvider(installed=True)
    bootstrap(p)
    assert p.installer_runs() == []
    assert any(constants.RUNTIME_MARKER in argv for argv, _ in p.execs)


def test_a_missing_runtime_runs_the_default_entrypoint_as_root():
    p = FakeProvider()
    bootstrap(p)
    (run,) = p.installer_runs()
    assert run[1] is True, "the installer needs root"
    assert _command(p).rstrip().endswith(constants.RUNTIME_URL)


def test_the_entrypoint_can_be_pointed_elsewhere_from_the_environment(monkeypatch):
    monkeypatch.setenv("OMELET_RUNTIME_URL", "https://example.invalid/branch/get.sh")
    p = FakeProvider()
    bootstrap(p)
    assert _command(p).rstrip().endswith("https://example.invalid/branch/get.sh")
    assert constants.RUNTIME_URL not in _command(p)


def test_a_ref_reaches_the_guest_only_when_one_is_set(monkeypatch):
    p = FakeProvider()
    bootstrap(p)
    assert "OMELET_RUNTIME_REF" not in _command(p)

    monkeypatch.setenv("OMELET_RUNTIME_REF", "feature/runtime-work")
    p = FakeProvider()
    bootstrap(p)
    assert "OMELET_RUNTIME_REF=feature/runtime-work" in _command(p)


def test_repair_reinstalls_an_installed_runtime_and_tells_the_installer_so():
    plain = FakeProvider()
    bootstrap(plain)
    assert "OMELET_RUNTIME_REPAIR" not in _command(plain)

    p = FakeProvider(installed=True)
    bootstrap(p, repair=True)
    assert "OMELET_RUNTIME_REPAIR=1" in _command(p)


def test_a_failing_installer_raises_with_the_guests_own_error():
    p = FakeProvider(fail=True, stderr="E: Unable to locate package docker-ce")
    with pytest.raises(BootstrapError) as excinfo:
        bootstrap(p)
    assert "docker-ce" in str(excinfo.value)


def test_an_installer_that_exits_zero_without_the_marker_is_a_failure():
    with pytest.raises(BootstrapError, match="runtime.version"):
        bootstrap(FakeProvider(writes_marker=False))


def test_a_value_that_would_be_re_split_on_the_guest_command_line_is_refused(monkeypatch):
    # The command crosses wsl.exe or ssh as one argument; a space or quote in
    # it would be parsed as shell by the guest.
    monkeypatch.setenv("OMELET_RUNTIME_REF", "main; rm -rf /")
    p = FakeProvider()
    with pytest.raises(BootstrapError, match="OMELET_RUNTIME_REF"):
        bootstrap(p)
    assert p.installer_runs() == []


def _fake_curl(tmp_path, body: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/sh\n" + body)
    curl.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _run_stub(tmp_path, env):
    p = FakeProvider()
    bootstrap(p)
    stub = tmp_path / "stub.sh"
    stub.write_text(_stub(_command(p)))
    return subprocess.run(["bash", str(stub), "https://example.invalid/get.sh"],
                          env=env, capture_output=True, text=True)


def test_the_stub_runs_the_script_it_downloaded(tmp_path):
    env = _fake_curl(tmp_path, "echo 'echo runtime-ran'\n")
    result = _run_stub(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "runtime-ran"


def test_a_broken_download_runs_nothing_and_says_so(tmp_path):
    # Piping curl straight into bash would execute whatever arrived before the
    # connection dropped.
    env = _fake_curl(tmp_path, "echo 'echo half-a-script'\nexit 22\n")
    result = _run_stub(tmp_path, env)
    assert result.returncode != 0
    assert "could not download" in result.stderr
    assert "half-a-script" not in result.stdout


def test_every_run_tells_the_installer_which_apis_this_host_speaks():
    p = FakeProvider()
    bootstrap(p)
    expected = ",".join(str(n) for n in sorted(constants.SUPPORTED_API))
    assert f"OMELET_RUNTIME_API={expected}" in _command(p)
    assert "OMELET_RUNTIME_UPDATE" not in _command(p)


def test_an_update_runs_on_an_installed_runtime_in_update_mode():
    p = FakeProvider(installed=True)
    bootstrap(p, update=True)
    assert "OMELET_RUNTIME_UPDATE=1" in _command(p)


def test_the_stub_hands_get_sh_the_url_it_came_from(tmp_path):
    env = _fake_curl(tmp_path, "echo 'echo \"from=$OMELET_RUNTIME_URL\"'\n")
    result = _run_stub(tmp_path, env)
    assert result.stdout.strip() == "from=https://example.invalid/get.sh"
