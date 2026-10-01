from pathlib import Path
from host.providers.lima import LimaProvider, find_limactl
import host.providers.lima_install as lima_install
from host.core.provider import Runtime


class FakeRunner:
    def __init__(self, stdout=b"", returncode=0):
        self.calls = []
        self._out, self._rc = stdout, returncode

    def __call__(self, argv):
        self.calls.append(argv)
        class R:
            returncode = self._rc
            stdout = self._out
            stderr = b""
        return R()


def _mac(version="14.5"):
    return lambda: (version, ("", "", ""), "arm64")


def make(runner, mac_ver=None):
    return LimaProvider(name="omelet-vm", config=Path("/tmp/omelet.yaml"),
                        limactl="limactl", runner=runner,
                        mac_ver=mac_ver or _mac())


def test_exec_uses_limactl_shell():
    r = FakeRunner(stdout=b"ok\n")
    make(r).exec(["uname", "-sr"])
    assert r.calls[-1] == ["limactl", "shell", "omelet-vm", "uname", "-sr"]


def test_exec_root_uses_sudo():
    r = FakeRunner()
    make(r).exec(["id", "-un"], root=True)
    assert r.calls[-1] == ["limactl", "shell", "omelet-vm", "sudo", "id", "-un"]


def test_create_calls_start_with_config():
    r = FakeRunner()
    make(r).create()
    assert r.calls[-1] == ["limactl", "start", "--name=omelet-vm",
                           "--tty=false", "/tmp/omelet.yaml"]


def test_a_failed_vm_command_raises_instead_of_reporting_success():
    # `_cmd()` returns a Completed and never raises, so an unchecked result is
    # a silent success -- `omelet vm start` printing "VM started." for a VM
    # limactl refused to boot. The WSL2 provider learned this the hard way.
    import pytest

    with pytest.raises(RuntimeError, match="could not be started"):
        make(FakeRunner(returncode=1)).start()
    with pytest.raises(RuntimeError, match="could not be created"):
        make(FakeRunner(returncode=1)).create()


def test_stop_and_destroy():
    r = FakeRunner()
    p = make(r)
    p.stop()
    assert r.calls[-1] == ["limactl", "stop", "omelet-vm"]
    p.destroy()
    assert r.calls[-1] == ["limactl", "delete", "omelet-vm"]


def test_the_lima_config_forwards_exactly_the_ports_the_host_dials():
    # The literals in omelet.yaml are the only thing making the guest sockets
    # reachable from the host. Both are declared equal on the two sides on
    # purpose; a distinct-port forward is made at runtime over ssh instead and
    # never belongs in this file. Held against the constants, not against
    # another copy of the literals.
    import re
    from pathlib import Path

    from host.core import constants

    config = (Path(__file__).resolve().parents[2] / "host" / "providers"
              / "omelet.yaml").read_text()
    pairs = {(int(g), int(h)) for g, h in re.findall(
        r"guestPort:\s*(\d+)\s*\n\s*hostPort:\s*(\d+)", config)}
    assert pairs == {(constants.EDGE_PORT, constants.EDGE_PORT),
                     (constants.API_PORT, constants.API_PORT)}


# --- finding limactl when there is no shell PATH ---
#
# The setup window is an app, and an app launched from Finder is started by
# LaunchServices with PATH=/usr/bin:/bin:/usr/sbin:/sbin. Homebrew is on
# neither prefix, so `shutil.which` alone reported Lima missing on a machine
# where `brew install lima` had just succeeded.


def test_limactl_is_found_on_the_path_when_there_is_one():
    from host.providers.lima import find_limactl
    assert find_limactl(which=lambda name: "/somewhere/bin/limactl") \
        == "/somewhere/bin/limactl"


def test_limactl_is_found_in_the_homebrew_prefix_without_a_path(tmp_path):
    from host.providers.lima import find_limactl

    brew = tmp_path / "homebrew" / "bin"
    brew.mkdir(parents=True)
    binary = brew / "limactl"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    assert find_limactl(which=lambda name: None, prefixes=(str(brew),)) == str(binary)


def test_a_missing_limactl_comes_back_as_the_bare_name(tmp_path):
    # Not None: is_supported() is the one place that reports Lima missing, and
    # it reports it by failing to resolve this value.
    from host.providers.lima import find_limactl
    assert find_limactl(which=lambda name: None, prefixes=(str(tmp_path),)) == "limactl"


def test_an_explicit_path_is_never_second_guessed():
    from host.providers.lima import find_limactl
    assert find_limactl("/opt/omelet/limactl", which=lambda name: "/usr/bin/limactl") \
        == "/opt/omelet/limactl"


def test_a_resolved_path_still_satisfies_the_installed_check(tmp_path):
    # is_supported() runs shutil.which over whatever it was handed, and which()
    # accepts an absolute path by checking that file directly -- so resolving
    # the binary must not turn the check into a permanent failure.
    binary = tmp_path / "limactl"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    provider = LimaProvider(
        limactl=str(binary),
        runner=FakeRunner(stdout=f"limactl version {lima_install.LIMA_VERSION}\n".encode()),
        mac_ver=_mac())
    assert provider.is_supported().ok


def test_a_different_lima_version_is_reported_as_not_installed(tmp_path):
    # The check used to be "is some limactl executable", which told a Mac with
    # Homebrew Lima 1.x -- no managed copy -- that "Lima 2.2.0" was present.
    # This branch made the version claim load-bearing, so the check must
    # actually run `--version` and compare, not just find an executable.
    binary = tmp_path / "limactl"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    provider = LimaProvider(limactl=str(binary),
                            runner=FakeRunner(stdout=b"limactl version 1.0.0\n"),
                            mac_ver=_mac())
    diagnosis = provider.is_supported()
    lima_check = next(c for c in diagnosis.checks if "Lima" in c.label)
    assert not lima_check.ok
    assert "setup" in lima_check.fix.lower()


# --- installing Lima instead of dead-ending on a missing one ---


def test_find_limactl_prefers_the_managed_copy_over_homebrew(tmp_path):
    managed = tmp_path / "lima" / "bin" / "limactl"
    managed.parent.mkdir(parents=True)
    managed.write_text("#!/bin/sh\n")
    managed.chmod(0o755)
    found = find_limactl(which=lambda name: "/opt/homebrew/bin/limactl",
                         managed=managed)
    assert found == str(managed)


def test_find_limactl_falls_back_to_the_path_before_setup_has_run(tmp_path):
    # A source checkout that has never run setup has no managed copy; a
    # developer's brew install is what makes `omelet doctor` answerable there.
    missing = tmp_path / "lima" / "bin" / "limactl"
    found = find_limactl(which=lambda name: "/opt/homebrew/bin/limactl",
                         managed=missing)
    assert found == "/opt/homebrew/bin/limactl"


def test_preflight_no_longer_dead_ends_on_a_missing_lima():
    # Installing Lima is setup's job now. A preflight that stops for it is
    # setup refusing to do its own work.
    diagnosis = make(FakeRunner()).preflight()
    assert diagnosis.dead_ends == []


def test_preflight_refuses_a_mac_too_old_for_the_virtualization_framework():
    # vz, which omelet.yaml asks Lima for, is macOS 13+.
    provider = LimaProvider(name="omelet-vm", runner=FakeRunner(),
                            mac_ver=_mac("12.7"))
    assert provider.preflight().dead_ends


def test_preflight_parses_mac_ver_the_same_way_on_every_named_case():
    cases = [
        # platform.mac_ver() returns "" off macOS; get_provider() only hands
        # out this provider on darwin, so a version we cannot read is our
        # ignorance, not evidence the Mac is too old.
        ("", True),
        # The SYSTEM_VERSION_COMPAT sentinel every unmanifested process sees
        # -- the true OS can be anything from Big Sur up, so this is
        # unparseable too, not a real major version 10.
        ("10.16", True),
        ("13", True),
        ("26.6.2", True),
        ("12.7", False),
    ]
    for release, expect_ok in cases:
        provider = LimaProvider(name="omelet-vm", runner=FakeRunner(),
                                mac_ver=_mac(release))
        assert provider.preflight().ok is expect_ok, release


def test_doctor_still_reports_a_missing_lima_and_names_setup_as_the_fix():
    provider = LimaProvider(name="omelet-vm", limactl="/nowhere/limactl",
                            runner=FakeRunner(), data_root=Path("/nowhere"),
                            mac_ver=_mac())
    diagnosis = provider.is_supported()
    lima_check = next(c for c in diagnosis.checks if "Lima" in c.label)
    assert not lima_check.ok
    assert "setup" in lima_check.fix.lower()
    assert "brew" not in lima_check.fix.lower()


def test_runtime_installs_into_the_providers_data_root(tmp_path, monkeypatch):
    seen = {}

    def fake_install(root, *, on_progress=None):
        seen["root"] = root
        seen["on_progress"] = on_progress
        return root / "lima" / "bin" / "limactl"

    monkeypatch.setattr(lima_install, "install", fake_install)
    provider = LimaProvider(name="omelet-vm", runner=FakeRunner(), data_root=tmp_path)
    runtime = provider.runtime()
    assert isinstance(runtime, Runtime)
    assert "Lima" in runtime.label
    emit = lambda done, total: None
    runtime.run(emit)
    assert seen == {"root": tmp_path, "on_progress": emit}


def test_runtime_rebinds_limactl_to_the_path_the_install_produced(monkeypatch):
    # find_limactl() ran before this download existed and resolved to the
    # bare name -- an app launched by LaunchServices has no PATH Homebrew is
    # on. Without the rebind, create_vm is the first thing to shell out to
    # "limactl" and fails with FileNotFoundError, on every first run.
    resolved = {}

    def fake_install(root, *, on_progress=None):
        resolved["on_progress"] = on_progress
        return root / "lima" / "bin" / "limactl"

    monkeypatch.setattr(lima_install, "install", fake_install)
    data_root = Path("/wherever")
    provider = LimaProvider(name="omelet-vm", limactl="limactl",
                            runner=FakeRunner(), data_root=data_root)
    emit = lambda done, total: None
    result = provider.runtime().run(emit)
    assert result is None, "the step's message must be text, never a Path"
    assert provider.limactl == str(data_root / "lima" / "bin" / "limactl")
    assert resolved["on_progress"] is emit


def test_the_wsl2_provider_has_nothing_to_install():
    from host.providers.wsl2 import Wsl2Provider
    assert Wsl2Provider(arch="amd64").runtime() is None


def test_running_reads_the_status_column():
    r = FakeRunner(stdout=b"Running\n")
    assert make(r).running() is True
    assert r.calls[-1] == ["limactl", "list", "--format", "{{.Status}}", "omelet-vm"]


def test_running_false_for_a_stopped_vm():
    assert make(FakeRunner(stdout=b"Stopped\n")).running() is False


def test_the_mac_installer_is_the_package_for_this_architecture():
    ran = []

    class Result:
        returncode, stdout, stderr = 0, b"", b""

    provider = LimaProvider(runner=lambda argv: ran.append(argv) or Result(),
                            machine=lambda: "arm64")
    assert provider.installer_asset("0.2.0") == "OmeletSetup-0.2.0-arm64.pkg"
    provider.launch_installer(Path("/cache/OmeletSetup-0.2.0-arm64.pkg"))
    assert ran == [["open", "/cache/OmeletSetup-0.2.0-arm64.pkg"]]
