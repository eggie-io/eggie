"""Distinct-port forwarding on both providers.

`forward()` used to raise NotImplementedError whenever the ports differed. The
edge port is equal on both sides by design, so that covered every case the PoC
had -- but reaching a database inside the VM from a host tool needs a host port
that is free on the host, which is rarely the guest's.

Assertions are about constructed argv. Nothing here runs netsh or ssh.
"""
from pathlib import Path

import pytest

from host.providers.lima import LimaProvider
from host.providers.wsl2 import ELEVATION_DECLINED, Wsl2Provider


class FakeRunner:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.calls = []
        self._out, self._err, self._rc = stdout, stderr, returncode

    def __call__(self, argv):
        self.calls.append(argv)
        class R:
            returncode = self._rc
            stdout = self._out
            stderr = self._err
        return R()


class FakeElevator:
    def __init__(self, code=0):
        self.calls = []
        self.code = code

    def __call__(self, exe, args):
        self.calls.append((exe, args))
        return self.code


def wsl(elevator=None, runner=None):
    return Wsl2Provider(distro="eggie-vm", wsl="wsl.exe",
                        runner=runner or FakeRunner(),
                        elevator=elevator or FakeElevator(),
                        facts=dict, arch="amd64")


def lima(runner=None):
    return LimaProvider(name="eggie-vm", config=Path("/tmp/eggie.yaml"),
                        lima_home=Path("/home/u/.lima"),
                        limactl="limactl", runner=runner or FakeRunner())


# --- equal ports: still a no-op on both platforms ---

def test_wsl2_equal_ports_stay_a_no_op():
    # WSL2 localhostForwarding already surfaces guest 0.0.0.0:<port> on host
    # localhost:<same port>. A portproxy rule on top would put a second hop in
    # front of the edge port for no gain -- and would need elevation to do it.
    e = FakeElevator()
    wsl(elevator=e).forward(39080, 39080)
    assert e.calls == []


def test_lima_equal_ports_stay_a_no_op():
    # Declared in eggie.yaml's portForwards; Lima sets it up at VM start.
    r = FakeRunner()
    lima(runner=r).forward(39080, 39080)
    assert r.calls == []


# --- WSL2 ---

def test_wsl2_distinct_ports_build_a_loopback_portproxy_rule():
    # Connecting to 127.0.0.1 rather than the VM's own address is deliberate:
    # localhostForwarding already puts the guest port on host loopback, and the
    # VM's address changes on every boot, which would leave a stale rule behind.
    e = FakeElevator()
    wsl(elevator=e).forward(5432, 5433)
    assert len(e.calls) == 1
    exe, args = e.calls[0]
    joined = " ".join(args)
    assert "netsh interface portproxy delete v4tov4 " \
           "listenaddress=127.0.0.1 listenport=5433" in joined
    assert "netsh interface portproxy add v4tov4 " \
           "listenaddress=127.0.0.1 listenport=5433 " \
           "connectaddress=127.0.0.1 connectport=5432" in joined


def test_wsl2_adding_the_same_forward_twice_is_idempotent():
    # netsh `add` fails on an existing listen address/port, and its message is
    # localized, so it cannot be matched. Deleting first makes the pair
    # idempotent by construction instead of by parsing an error.
    e = FakeElevator()
    provider = wsl(elevator=e)
    provider.forward(5432, 5433)
    provider.forward(5432, 5433)
    assert len(e.calls) == 2
    assert e.calls[0] == e.calls[1]


def test_wsl2_removing_a_forward_that_is_not_there_is_not_an_error():
    e = FakeElevator(code=1)   # netsh delete exits non-zero for a missing rule
    wsl(elevator=e).unforward(5432, 5433)
    assert len(e.calls) == 1


def test_wsl2_a_forward_that_could_not_be_added_is_loud():
    e = FakeElevator(code=1)
    with pytest.raises(RuntimeError, match="5433"):
        wsl(elevator=e).forward(5432, 5433)


def test_wsl2_a_declined_uac_prompt_says_what_to_do():
    e = FakeElevator(code=ELEVATION_DECLINED)
    with pytest.raises(RuntimeError, match="administrator"):
        wsl(elevator=e).forward(5432, 5433)


def test_wsl2_lists_the_forwards_windows_kept_across_the_reboot():
    # netsh's table is stored in the registry and survives a restart, so it is
    # the record of what was allocated -- and the only thing that can be used
    # to release it. Header rows are localized; the four-column shape is not.
    out = (
        "Listen on ipv4:             Connect to ipv4:\r\n\r\n"
        "Address         Port        Address         Port\r\n"
        "--------------- ----------  --------------- ----------\r\n"
        "127.0.0.1       5433        127.0.0.1       5432\r\n"
        "127.0.0.1       6380        127.0.0.1       6379\r\n"
    ).encode("utf-8")
    r = FakeRunner(stdout=out)
    assert wsl(runner=r).forwards() == [(5432, 5433), (6379, 6380)]
    assert r.calls[-1] == ["netsh", "interface", "portproxy", "show", "v4tov4"]


# --- Lima ---

def test_lima_distinct_ports_use_the_control_socket_lima_already_maintains():
    r = FakeRunner()
    lima(runner=r).forward(5432, 5433)
    assert r.calls == [
        ["ssh", "-F", "/home/u/.lima/eggie-vm/ssh.config",
         "-O", "cancel", "-L", "5433:127.0.0.1:5432", "lima-eggie-vm"],
        ["ssh", "-F", "/home/u/.lima/eggie-vm/ssh.config",
         "-O", "forward", "-L", "5433:127.0.0.1:5432", "lima-eggie-vm"],
    ]


def test_lima_adding_the_same_forward_twice_is_idempotent():
    r = FakeRunner()
    provider = lima(runner=r)
    provider.forward(5432, 5433)
    provider.forward(5432, 5433)
    assert r.calls[:2] == r.calls[2:]


def test_lima_removing_a_forward_that_is_not_there_is_not_an_error():
    r = FakeRunner(returncode=255)
    lima(runner=r).unforward(5432, 5433)
    assert r.calls == [
        ["ssh", "-F", "/home/u/.lima/eggie-vm/ssh.config",
         "-O", "cancel", "-L", "5433:127.0.0.1:5432", "lima-eggie-vm"]]


def test_lima_a_forward_that_could_not_be_added_is_loud():
    r = FakeRunner(returncode=255, stderr=b"mux_client_forward: forwarding request failed")
    with pytest.raises(RuntimeError, match="5433"):
        lima(runner=r).forward(5432, 5433)


def test_lima_keeps_no_forwards_across_a_vm_restart():
    # The control master dies with the VM, so nothing can be leaked and there
    # is nothing to release. Present so callers can ask both providers alike.
    assert lima().forwards() == []


# --- parity ---

def test_both_providers_expose_the_same_forwarding_surface():
    for name in ("forward", "unforward", "forwards"):
        assert callable(getattr(Wsl2Provider, name, None)), f"Wsl2Provider.{name}"
        assert callable(getattr(LimaProvider, name, None)), f"LimaProvider.{name}"
