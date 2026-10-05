from typer.testing import CliRunner
import host.cli as cli
from host.core.provider import Completed

runner = CliRunner()


class FakeProvider:
    def __init__(self, exists=False):
        self._exists = exists
        self.created = False
        self.execs = []

    def exists(self): return self._exists
    def create(self): self.created = True
    def start(self): pass
    def stop(self): pass
    def destroy(self): pass
    def exec(self, argv, *, root=False):
        self.execs.append(argv)
        # the runtime marker exists, so bootstrap is a no-op
        if argv[:2] == ["test", "-s"]:
            return Completed(0, "", "")
        return Completed(0, "", "")
    def forward(self, g, h): pass
    def is_supported(self): ...


def test_vm_create_creates_when_absent(monkeypatch):
    fake = FakeProvider(exists=False)
    monkeypatch.setattr(cli, "_provider_factory", lambda: fake)
    result = runner.invoke(cli.app, ["vm", "create"])
    assert result.exit_code == 0
    assert fake.created is True
    assert "VM ready." in result.stdout


def test_vm_create_fails_loudly_when_guest_bootstrap_fails(monkeypatch):
    class FailingProvider(FakeProvider):
        def exec(self, argv, *, root=False):
            self.execs.append(argv)
            if argv[:2] == ["test", "-s"]:
                return Completed(1, "", "")          # not installed yet
            return Completed(1, "", "E: Unable to locate package docker-ce")

    monkeypatch.setattr(cli, "_provider_factory", lambda: FailingProvider(exists=True))
    result = runner.invoke(cli.app, ["vm", "create"])
    assert result.exit_code == 1
    assert "VM ready." not in result.stdout
    assert "docker-ce" in result.stdout


def test_vm_create_skips_create_when_present(monkeypatch):
    fake = FakeProvider(exists=True)
    monkeypatch.setattr(cli, "_provider_factory", lambda: fake)
    result = runner.invoke(cli.app, ["vm", "create"])
    assert result.exit_code == 0
    assert fake.created is False


def test_vm_start_reports_the_providers_own_failure_instead_of_saying_started(monkeypatch):
    # `wsl.exe` failing is a Completed with a non-zero code, not an exception:
    # dropping it printed "VM started." for a VM that does not exist.
    class BrokenProvider(FakeProvider):
        def start(self):
            raise RuntimeError("the virtual machine 'eggie-vm' could not be "
                               "started (exit 1): no such distribution")

    monkeypatch.setattr(cli, "_provider_factory", lambda: BrokenProvider())
    result = runner.invoke(cli.app, ["vm", "start"])
    assert result.exit_code == 1
    assert "VM started." not in result.stdout
    assert "no such distribution" in result.output
    assert "Traceback" not in result.output
