"""Which screen a probe result opens.

`probe()` returns early at every stage, so each Readiness shape below is
exactly one `return` in host/core/status.py. The rows are mutually exclusive;
getting one wrong shows the user a screen whose words are false.
"""
from __future__ import annotations

from host.core.status import Readiness
from host.desktop.view import route_for


def test_provider_that_could_not_be_asked_is_not_not_installed():
    # `provider.exists()` threw -- no limactl on PATH. vm_exists is False, the
    # same shape as "no VM yet", but telling this user to press "Set up the
    # kitchen" sends them at a setup that cannot run.
    assert route_for(Readiness(problem="limactl not found")) == ("home", "wrong")


def test_no_vm_is_not_installed():
    assert route_for(Readiness()) == ("home", "not_installed")


def test_vm_present_but_exec_threw_is_wrong_not_stopped():
    assert route_for(Readiness(vm_exists=True, problem="wsl.exe: access denied")) \
        == ("home", "wrong")


def test_vm_present_and_quiet_is_stopped():
    assert route_for(Readiness(vm_exists=True)) == ("home", "stopped")


def test_reachable_without_runtime_is_wrong():
    assert route_for(Readiness(vm_exists=True, vm_reachable=True)) == ("home", "wrong")


def test_a_marker_read_that_threw_is_wrong_not_unreachable():
    # probe()'s third stage: the VM answers, but reading runtime.version threw.
    # The unreachable screen claims we can see the machine humming, which
    # needs a confirmed runtime -- so this belongs on "something's wrong",
    # and it is what pins the `and readiness.runtime_version` half of the
    # unreachable guard.
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          problem="cat: /opt/eggie/runtime.version: No such file")
    assert route_for(readiness) == ("home", "wrong")


def test_api_refused_is_the_only_unreachable():
    # The one state where the board's copy is literally true: the VM answers
    # `true`, the runtime marker is there, and only /health is silent.
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          runtime_version="runtime-v0.1.0",
                          problem="connection refused")
    assert route_for(readiness) == ("unreachable", "")


def test_an_installed_runtime_on_an_unsupported_api_is_updated():
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          runtime_version="runtime-v9.0.0", api_version=99)
    assert route_for(readiness) == ("update_runtime", "")


def test_ready_is_running():
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          runtime_version="runtime-v0.1.0", api_version=1)
    assert route_for(readiness) == ("home", "running")


def test_a_vm_that_stopped_answering_is_unresponsive_not_unreachable():
    # Same facts as the unreachable row above, but Repair works through the
    # very command that is hanging, so offering it would spin forever.
    readiness = Readiness(vm_exists=True, vm_reachable=True,
                          runtime_version="runtime-v0.1.0",
                          problem="Wsl/Service/CreateInstance/0x8007274c",
                          unresponsive=True)
    assert route_for(readiness) == ("unresponsive", "")
