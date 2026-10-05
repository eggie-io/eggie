"""The wiring of the real step list — the seam where findings survived review.

Every other install test builds its own toy steps, so nothing exercised the
list `omelet setup` actually runs.
"""
import pytest

from host.core.images import Image
from host.core.install import InstallError, InstallState, default_steps, run_install
from host.core.provider import CheckResult, Diagnosis, Runtime

IMAGE = Image("https://example.invalid/ubuntu-24.04.4-wsl-amd64.wsl", "0" * 64)


class FakeProvider:
    location = r"C:\Users\you\AppData\Local\Omelet\vm"
    terminal = "PowerShell"
    remediable = True
    runtime_value = None
    ssh_value = None

    def __init__(self, *, exists=True, reboot=False):
        self.rootfs = None
        self._exists = exists
        self._reboot = reboot
        self.created = False
        self.started = False
        self.resumed_with = None

    def preflight(self): return Diagnosis([CheckResult("fine", True)])
    def apply_remedy(self, remedy): pass
    def reboot_required(self): return self._reboot
    def register_resume(self, exe): self.resumed_with = exe
    def image(self): return IMAGE
    def exists(self): return self._exists
    def runtime(self): return self.runtime_value
    def ssh_shortcut(self): return self.ssh_value

    def create(self):
        if self.rootfs is None:
            raise ValueError("install_dir and rootfs are required to create the VM")
        self.created = True

    def start(self):
        self.started = True


class SelfImagingProvider(FakeProvider):
    """A provider on a host that needs nothing turned on and fetches its own
    guest image -- Lima. The install list must not contain the steps that would
    do either, rather than showing steps that quietly do nothing."""

    location = "/Users/you/.lima/omelet-vm"
    terminal = "Terminal"
    remediable = False

    def image(self):
        return None


def build(provider, tmp_path, **overrides):
    kwargs = dict(
        cache_dir=tmp_path / "cache",
        template_dir=tmp_path / "template",
        domain="127-0-0-1.sslip.io",
        exe_path=r"C:\Apps\Omelet\setup.exe",
    )
    return default_steps(provider, **{**kwargs, **overrides})


def run(steps, state, patch):
    """Run the real list, substituting the steps that would touch a real VM."""
    events = []
    stubbed = [s if s.name not in patch else type(s)(
        s.name, patch[s.name], always_run=s.always_run, action=s.action,
        label=s.label, progress=s.progress)
        for s in steps]
    run_install(stubbed, state, events.append)
    return events


def test_step_names_and_order_match_the_spec(tmp_path):
    names = [s.name for s in build(FakeProvider(), tmp_path)]
    assert names == ["preflight", "remediate", "reboot_gate", "fetch_image",
                     "create_vm", "bootstrap", "connect", "verify", "finish"]


def test_fetch_image_is_built_as_a_progress_step(tmp_path):
    # The only step long enough to need a fraction. Asserted directly here
    # rather than left to be caught only by the emitter's argument count.
    steps = {s.name: s for s in build(FakeProvider(), tmp_path)}
    assert steps["fetch_image"].progress is True
    assert all(s.progress is False for name, s in steps.items() if name != "fetch_image")


def test_rootfs_is_set_outside_the_download_step(tmp_path):
    # The bug: rootfs was only assigned as a side effect inside fetch_image, so
    # any re-run where that step did not assign it reached create_vm with
    # rootfs=None forever.
    state = InstallState(tmp_path / "state.json")
    for name in ("preflight", "remediate", "reboot_gate", "fetch_image"):
        state.mark(name)

    provider = FakeProvider(exists=False)
    steps = build(provider, tmp_path)
    run(steps, state, {"fetch_image": lambda emit: None, "bootstrap": lambda: None,
                       "connect": lambda: None, "verify": lambda: None})

    assert provider.rootfs == tmp_path / "cache" / "ubuntu-24.04.4-wsl-amd64.wsl"
    assert provider.created, "create_vm must succeed on a re-run, not raise on a None rootfs"


def test_the_proving_steps_run_again_on_a_re_run(tmp_path):
    # A user whose VM broke re-runs setup; skipping verify would report success
    # while proving nothing, and the VM's API can have changed since the run
    # that recorded the compatibility check.
    state = InstallState(tmp_path / "state.json")
    provider = FakeProvider()
    ran = []
    patch = {"fetch_image": lambda emit: None,
             "bootstrap": lambda: None,
             "connect": lambda: ran.append("connect"),
             "verify": lambda: ran.append("verify"),
             "finish": lambda: ran.append("finish") or "done"}

    run(build(provider, tmp_path), state, patch)
    assert ran == ["connect", "verify", "finish"]
    assert "verify" not in state.completed(), \
        "a proof that only holds for one run must not be persisted"

    ran.clear()
    events = run(build(provider, tmp_path), state, patch)
    assert ran == ["connect", "verify", "finish"], "verify must never be skipped"
    assert [e.step for e in events if e.status == "skipped"] == \
        ["preflight", "remediate", "reboot_gate"]


def test_finish_names_the_install_location_and_the_next_command(tmp_path):
    state = InstallState(tmp_path / "state.json")
    provider = FakeProvider()
    events = run(build(provider, tmp_path), state,
                 {"fetch_image": lambda emit: None, "bootstrap": lambda: None,
                  "connect": lambda: None, "verify": lambda: None})
    finish = next(e for e in events if e.step == "finish" and e.status == "done")
    assert provider.location in finish.message
    assert "omelet up" in finish.message
    assert "succe" in finish.message.lower()


def test_finish_names_the_place_the_vm_really_is_and_this_platform_s_terminal(tmp_path):
    # The message used to be written for Windows on both platforms: it named the
    # host's own install dir, which on macOS is an empty folder Lima never
    # touches, and told a Mac user to open PowerShell.
    state = InstallState(tmp_path / "state.json")
    provider = SelfImagingProvider()
    events = run(build(provider, tmp_path), state,
                 {"bootstrap": lambda: None, "connect": lambda: None,
                  "verify": lambda: None})
    finish = next(e for e in events if e.step == "finish" and e.status == "done")
    assert "/Users/you/.lima/omelet-vm" in finish.message
    assert "open Terminal" in finish.message
    assert "PowerShell" not in finish.message


def test_a_provider_that_fetches_its_own_image_gets_no_download_step(tmp_path):
    # Lima downloads the image named in omelet.yaml itself. Keeping fetch_image
    # in the list would mean calling provider.image() for a URL nobody reads --
    # and LimaProvider had no image() at all, so `omelet setup` on macOS died
    # with an AttributeError before its first step ran.
    provider = SelfImagingProvider()
    names = [s.name for s in build(provider, tmp_path)]
    assert "fetch_image" not in names
    assert provider.rootfs is None, "there is no rootfs for the host to place"


def test_a_host_with_nothing_to_turn_on_gets_no_remediation_or_restart(tmp_path):
    # The setup window listed every step in this list. On a Mac it therefore
    # showed "Turning on Windows features" and "Restart needed", both of which
    # would have been skipped and neither of which exists on that platform.
    names = [s.name for s in build(SelfImagingProvider(), tmp_path)]
    assert names == ["preflight", "create_vm", "bootstrap", "connect",
                     "verify", "finish"]


def test_a_provider_with_a_runtime_gets_an_install_step_after_preflight(tmp_path):
    provider = SelfImagingProvider()
    calls = []
    provider.runtime_value = Runtime("Installing Lima", lambda emit: calls.append(emit))
    names = [s.name for s in build(provider, tmp_path)]
    assert names[:2] == ["preflight", "install_runtime"]
    assert "fetch_image" not in names


def test_the_install_step_carries_the_providers_own_label(tmp_path):
    provider = SelfImagingProvider()
    provider.runtime_value = Runtime("Installing Lima", lambda emit: None)
    step = next(s for s in build(provider, tmp_path) if s.name == "install_runtime")
    assert step.label == "Installing Lima"
    assert step.progress is True
    # Never recorded as done: what it produces is a directory a user can
    # delete, and re-deriving it costs one file read.
    assert step.always_run is True
    assert step.action, "a failed download needs a sentence telling the user what to do"


def test_a_provider_with_no_runtime_gets_no_install_step(tmp_path):
    assert "install_runtime" not in [s.name for s in build(FakeProvider(), tmp_path)]


def test_a_failure_carries_a_suggested_action_not_just_the_raw_error(tmp_path):
    state = InstallState(tmp_path / "state.json")

    def boom(emit):
        raise OSError("<urlopen error [Errno 11001] getaddrinfo failed>")

    with pytest.raises(InstallError) as excinfo:
        run(build(FakeProvider(), tmp_path), state, {"fetch_image": boom})

    assert excinfo.value.step == "fetch_image"
    assert "getaddrinfo" in excinfo.value.message, "support still needs the raw detail"
    assert "internet" in excinfo.value.action.lower()
    assert "urlopen" not in excinfo.value.action, "the action must be jargon-free"


def test_the_gate_registers_resume_before_asking_for_a_restart(tmp_path):
    from host.core.install import RebootRequired

    provider = FakeProvider(reboot=True)
    state = InstallState(tmp_path / "state.json")
    with pytest.raises(RebootRequired):
        run(build(provider, tmp_path), state, {})
    assert provider.resumed_with == r"C:\Apps\Omelet\setup.exe"


def test_create_vm_starts_an_existing_but_stopped_vm(tmp_path):
    # `omelet setup`'s status summary tells a user with a stopped VM to "run
    # setup again to start it", but nothing in this list ever called start()
    # on a VM that merely existed -- Lima's `shell` refuses a stopped
    # instance outright, and only WSL2 hid the gap (`wsl.exe -d` auto-starts
    # a distro). create_vm must ensure the VM is *running*, not just present.
    state = InstallState(tmp_path / "state.json")
    provider = FakeProvider(exists=True)
    run(build(provider, tmp_path), state,
        {"fetch_image": lambda emit: None, "bootstrap": lambda: None,
         "connect": lambda: None, "verify": lambda: None})
    assert provider.started, "an existing VM must be started, not left alone"
    assert not provider.created, "an existing VM must not be recreated"


def test_a_vm_destroyed_outside_setup_is_rebuilt_on_a_re_run(tmp_path):
    # The bug: install-state.json outlives the VM. A user who destroyed the
    # distro and ran setup again saw "Creating the virtual machine ✓" (skipped)
    # and then WSL_E_DISTRO_NOT_FOUND from bootstrap's first guest command.
    state = InstallState(tmp_path / "state.json")
    for name in ("preflight", "remediate", "reboot_gate", "fetch_image",
                 "create_vm", "bootstrap"):
        state.mark(name)

    provider = FakeProvider(exists=False)
    ran = []
    events = run(build(provider, tmp_path), state,
                 {"fetch_image": lambda emit: ran.append("fetch_image"),
                  "bootstrap": lambda: ran.append("bootstrap"),
                  "connect": lambda: None, "verify": lambda: None})

    assert provider.created, "a missing VM must be created, whatever the state file says"
    assert ran == ["fetch_image", "bootstrap"], \
        "the rootfs and the guest stack must be re-derived, not assumed"
    assert [e.step for e in events if e.status == "skipped"] == \
        ["preflight", "remediate", "reboot_gate"], \
        "only facts about this computer may be remembered across runs"
