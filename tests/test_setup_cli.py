import pytest
from typer.testing import CliRunner

import host.cli as cli
from host.core.images import Image
from host.core.install import DeadEnd, RebootRequired
from host.core.provider import Access, CheckResult, Completed, Diagnosis

runner = CliRunner()


class StubProvider:
    location = r"C:\Users\you\AppData\Local\Omelet\vm"
    terminal = "PowerShell"
    remediable = True
    autostart_off = False

    def __init__(self, diagnosis=None, reboot=False):
        self._diagnosis = diagnosis or Diagnosis([CheckResult("all good", True)])
        self._reboot = reboot
        self.resumed_with = None

    def preflight(self): return self._diagnosis
    def apply_remedy(self, remedy): pass
    def reboot_required(self): return self._reboot
    def register_resume(self, exe): self.resumed_with = exe
    def exists(self): return True
    def create(self): pass
    def start(self): pass
    def exec(self, argv, *, root=False): return Completed(0, "", "")
    def destroy(self): pass
    def set_autostart(self, on, exe): self.autostart_off = (on is False)
    def image(self): return Image("http://example.invalid/img.wsl", "0" * 64)
    def runtime(self): return None
    def access(self): return Access(headline="Connect", summary="", command="wsl")


class FailingDestroyProvider(StubProvider):
    def destroy(self):
        raise RuntimeError("wsl.exe not found")


def test_setup_reports_a_dead_end_in_plain_language(monkeypatch):
    blocked = Diagnosis([CheckResult(
        "CPU virtualization available", False,
        fix="Restart into BIOS/UEFI setup and enable Intel VT-x or AMD-V")])
    monkeypatch.setattr(cli, "_provider_factory", lambda: StubProvider(blocked))
    result = runner.invoke(cli.app, ["setup", "--headless"])
    assert result.exit_code == 1
    assert "BIOS" in result.stdout
    assert "Traceback" not in result.stdout, "non-technical users must not see a stack trace"


def test_setup_registers_resume_and_asks_for_a_restart(monkeypatch):
    provider = StubProvider(reboot=True)
    monkeypatch.setattr(cli, "_provider_factory", lambda: provider)
    result = runner.invoke(cli.app, ["setup", "--headless"])
    assert result.exit_code == 2, "a pending reboot is not a failure"
    assert "Restart your computer" in result.stdout
    assert provider.resumed_with is not None


@pytest.fixture
def provisionable(monkeypatch):
    """Everything that would touch the network or a real VM, stubbed."""
    import host.core.bootstrap as bootstrap_mod
    import host.core.download as download_mod
    import host.core.install as install_mod

    monkeypatch.setattr(cli, "_provider_factory", lambda: StubProvider())
    monkeypatch.setattr(download_mod, "fetch", lambda image, dest, on_progress=None: dest)
    monkeypatch.setattr(bootstrap_mod, "bootstrap",
                        lambda provider, **kwargs: None)
    monkeypatch.setattr(install_mod, "connect_step", lambda *a, **k: None)
    monkeypatch.setattr(install_mod, "verify_step", lambda *a, **k: None)
    return monkeypatch


def test_headless_setup_ends_by_naming_the_next_command(provisionable):
    result = runner.invoke(cli.app, ["setup", "--headless"])
    assert result.exit_code == 0
    assert "omelet up" in result.stdout, \
        "a user who waited several minutes must be told what to type next"


def test_resume_explains_why_setup_started_by_itself(provisionable):
    result = runner.invoke(cli.app, ["setup", "--headless", "--resume"])
    assert "Continuing setup after the restart" in result.stdout


def test_a_failure_offers_a_suggested_action(provisionable):
    import host.core.bootstrap as bootstrap_mod

    def explode(provider, **kwargs):
        raise RuntimeError("apt-get: Temporary failure resolving 'archive.ubuntu.com'")

    provisionable.setattr(bootstrap_mod, "bootstrap", explode)

    result = runner.invoke(cli.app, ["setup", "--headless"])

    assert result.exit_code == 1
    assert "archive.ubuntu.com" in result.stdout, "support still needs the raw error"
    assert "What to do" in result.stdout
    assert "Traceback" not in result.stdout


def test_headless_setup_uses_the_providers_label_and_prints_progress(monkeypatch):
    # Two things `_emitter`'s whole-percent throttling still left invisible in
    # a terminal: 391 lines for a 391 MB download (fixed by printing on a 10%
    # boundary instead of every percent), and the provider's own step words --
    # "Installing Lima 2.2.0" is Lima's sentence, not "install_runtime".
    import host.core.bootstrap as bootstrap_mod
    import host.core.install as install_mod
    from host.core.provider import Runtime

    class LimaLikeProvider(StubProvider):
        remediable = False

        def image(self):
            return None

        def runtime(self):
            def run(emit):
                for done in (10, 55, 100):
                    emit(done, 100)
            return Runtime("Installing Lima 2.2.0", run)

    monkeypatch.setattr(cli, "_provider_factory", lambda: LimaLikeProvider())
    monkeypatch.setattr(bootstrap_mod, "bootstrap", lambda provider, **kwargs: None)
    monkeypatch.setattr(install_mod, "connect_step", lambda *a, **k: None)
    monkeypatch.setattr(install_mod, "verify_step", lambda *a, **k: None)

    result = runner.invoke(cli.app, ["setup", "--headless"])

    assert result.exit_code == 0
    assert "Installing Lima 2.2.0" in result.stdout
    assert "install_runtime" not in result.stdout
    percent_lines = [line for line in result.stdout.splitlines() if "%" in line]
    assert percent_lines, "a long step must show some progress, not silence until it ends"
    assert "100%" in result.stdout


def test_uninstall_requires_purge_to_destroy_the_vm(monkeypatch):
    monkeypatch.setattr(cli, "_provider_factory", lambda: StubProvider())
    result = runner.invoke(cli.app, ["uninstall"])
    assert result.exit_code == 1
    assert "--purge" in result.stdout


def test_uninstall_cleans_up_local_state_even_when_destroy_fails(monkeypatch):
    from host.core.install import InstallState
    from host.providers import default_install_dir

    monkeypatch.setattr(cli, "_provider_factory", lambda: FailingDestroyProvider())
    root = default_install_dir().parent
    state = InstallState(root / "install-state.json")
    state.mark("preflight")
    cache_dir = root / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "image.tar").write_text("x")

    result = runner.invoke(cli.app, ["uninstall", "--purge"])

    assert result.exit_code != 0, "a failed destroy must not be reported as success"
    assert "Traceback" not in result.stdout, "non-technical users must not see a stack trace"
    assert state.completed() == set(), "local state must be cleared even if destroy fails"
    assert not cache_dir.exists(), "the cache must be removed even if destroy fails"


def test_uninstall_purge_removes_the_vm_directory(monkeypatch):
    # The multi-gigabyte vhdx is what survives a failed destroy and fills the
    # disk. Project state is no longer host-side at all -- it lives inside the
    # VM at /opt/omelet/state.db and goes with it.
    from host.providers import default_install_dir

    monkeypatch.setattr(cli, "_provider_factory", lambda: FailingDestroyProvider())
    install_dir = default_install_dir()
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "ext4.vhdx").write_text("x")

    runner.invoke(cli.app, ["uninstall", "--purge"])

    assert not install_dir.exists(), "the VM directory must not survive a purge"


def test_uninstall_purge_removes_the_managed_lima_install(monkeypatch):
    # setup's install_runtime step puts the managed Lima under
    # default_install_dir().parent / "lima" (lima_install.managed_root) --
    # about 100 MB --purge never actually removed.
    from host.providers import default_install_dir

    monkeypatch.setattr(cli, "_provider_factory", lambda: FailingDestroyProvider())
    root = default_install_dir().parent
    lima_dir = root / "lima" / "bin"
    lima_dir.mkdir(parents=True, exist_ok=True)
    (lima_dir / "limactl").write_text("x")

    runner.invoke(cli.app, ["uninstall", "--purge"])

    assert not (root / "lima").exists(), "the managed Lima install must not survive a purge"


def test_uninstall_purge_succeeds_and_clears_state(monkeypatch):
    from host.core.install import InstallState
    from host.providers import default_install_dir

    monkeypatch.setattr(cli, "_provider_factory", lambda: StubProvider())
    root = default_install_dir().parent
    state = InstallState(root / "install-state.json")
    state.mark("preflight")

    result = runner.invoke(cli.app, ["uninstall", "--purge"])

    assert result.exit_code == 0
    assert state.completed() == set()


def test_selfcheck_reports_ok_for_every_bundled_asset():
    result = runner.invoke(cli.app, ["selfcheck"])
    assert result.exit_code == 0
    for name in ("docker-compose.yml", "omelet.yaml"):
        assert name in result.stdout
    assert "MISSING" not in result.stdout


def test_selfcheck_reports_missing_and_exits_nonzero_when_an_asset_cannot_resolve(
        monkeypatch, tmp_path):
    import host.providers as providers

    # Simulate a frozen build whose datas entry for omelet.yaml went missing:
    # __file__ is what the real resolution (Path(__file__).parent) depends on.
    monkeypatch.setattr(providers, "__file__", str(tmp_path / "nonexistent" / "__init__.py"))

    result = runner.invoke(cli.app, ["selfcheck"])

    assert result.exit_code == 1
    assert "MISSING" in result.stdout
    assert "omelet.yaml" in result.stdout


def test_verify_template_resolves_to_the_bundled_compose_file():
    from host.core.install import VERIFY_TEMPLATE
    assert (VERIFY_TEMPLATE / "docker-compose.yml").is_file()


def test_cli_never_resolves_bundled_assets_from_its_own_file():
    # cli.py is the frozen entry script, and PyInstaller gives it a __file__
    # under the bundle root rather than under host/. Resolving an asset
    # from it therefore succeeds from source and silently misses in a build --
    # which is exactly how the nginx-hello template shipped missing once.
    from pathlib import Path
    src = Path("host/cli.py").read_text()
    assert "Path(__file__)" not in src, \
        "resolve bundled assets from an imported module, not from cli.py"


def test_setup_hands_the_window_a_factory_it_can_call_twice(monkeypatch, tmp_path):
    """Re-run setup must build a fresh step list.

    The steps close over provider state (`provider.rootfs` is assigned while
    the list is built), so handing the window one list and running it twice
    would re-run the second install against the first one's bindings. Calling
    steps_factory() twice and discarding the results only proves the
    parameter is callable twice -- a `lambda: shared_list` closing over one
    list passes that identically, so this keeps both results and asserts
    they are distinct objects.
    """
    captured = {}
    provider = StubProvider()

    def fake_run(provider, state, *, steps_factory=None, resumed=False, background=False):
        captured["provider"] = provider
        captured["a"] = steps_factory()
        captured["b"] = steps_factory()
        captured["resumed"] = resumed
        captured["background"] = background
        return 0

    monkeypatch.setattr("host.desktop.__main__.run", fake_run)
    monkeypatch.setattr(cli, "_provider_factory", lambda: provider)
    monkeypatch.setattr("host.providers.default_install_dir",
                        lambda: tmp_path / "vm")

    result = runner.invoke(cli.app, ["setup"])
    assert result.exit_code == 0
    assert captured["resumed"] is False
    assert captured["provider"] is provider
    assert captured["a"] is not captured["b"], \
        "steps_factory must build a fresh list on every call, not close over one"


def test_setup_resume_reaches_the_window_as_resumed(monkeypatch, tmp_path):
    """--resume must reach the window, not just the headless path -- the
    router's own copy of `resumed` is what decides whether the wizard opens
    with "Continuing setup after the restart"."""
    captured = {}

    def fake_run(provider, state, *, steps_factory=None, resumed=False, background=False):
        captured["resumed"] = resumed
        captured["background"] = background
        return 0

    monkeypatch.setattr("host.desktop.__main__.run", fake_run)
    monkeypatch.setattr(cli, "_provider_factory", lambda: StubProvider())
    monkeypatch.setattr("host.providers.default_install_dir",
                        lambda: tmp_path / "vm")

    result = runner.invoke(cli.app, ["setup", "--resume"])
    assert result.exit_code == 0
    assert captured["resumed"] is True


def test_packaging_spec_bundles_exactly_the_assets_selfcheck_verifies():
    # selfcheck's whole job is catching a bad PyInstaller `datas` entry by
    # running the exe. That only works while the two lists agree: when
    # traefik.yml was deleted, the spec kept bundling a file that no longer
    # existed and selfcheck had quietly lost its entry, so nothing failed.
    import re
    from pathlib import Path

    spec = Path("packaging/windows/omelet.spec").read_text()
    datas = re.search(r"datas=\[(.*?)\n    \]", spec, re.DOTALL)
    assert datas, "could not find the datas block in the spec"
    bundled = set(re.findall(r'"\.\./\.\./([^"]+)"', datas[1]))

    result = runner.invoke(cli.app, ["selfcheck"])
    checked = set(re.findall(r"^\S+\s+(\S+) ->", result.stdout, re.MULTILINE))

    assert bundled == checked, \
        f"only bundled: {bundled - checked}; only selfchecked: {checked - bundled}"


def test_setup_background_reaches_the_window(monkeypatch, tmp_path):
    captured = {}

    def fake_run(provider, state, *, steps_factory=None, resumed=False, background=False):
        captured["background"] = background
        return 0

    monkeypatch.setattr(cli, "_provider_factory", lambda: StubProvider())
    monkeypatch.setattr("host.desktop.__main__.run", fake_run)
    result = runner.invoke(cli.app, ["setup", "--background"])
    assert result.exit_code == 0
    assert captured["background"] is True


def test_uninstall_turns_open_at_login_off(monkeypatch):
    provider = StubProvider()
    monkeypatch.setattr(cli, "_provider_factory", lambda: provider)
    result = runner.invoke(cli.app, ["uninstall", "--purge"])
    assert result.exit_code == 0
    assert provider.autostart_off is True


def test_uninstall_goes_on_when_autostart_cannot_be_removed(monkeypatch):
    class Stuck(StubProvider):
        def set_autostart(self, on, exe):
            raise OSError("registry locked")
    monkeypatch.setattr(cli, "_provider_factory", lambda: Stuck())
    assert runner.invoke(cli.app, ["uninstall", "--purge"]).exit_code == 0
