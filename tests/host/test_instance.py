from __future__ import annotations

import shutil
import socket
import tempfile
import threading
import uuid

import pytest

from host.providers.instance import claim


@pytest.fixture
def address():
    # Linux abstract namespace: no socket file, so multiprocessing has nothing
    # to unlink at exit after the test is gone (its finalizer printed tracebacks).
    return f"\0omelet-test-{uuid.uuid4().hex}"


def test_the_first_instance_claims_and_a_second_is_turned_away(address):
    shown = threading.Event()
    assert claim(address, "AF_UNIX", shown.set, announce=True) is True
    assert claim(address, "AF_UNIX", lambda: None, announce=True) is False
    assert shown.wait(timeout=5), "the first instance was never asked to show"


def test_a_background_second_instance_does_not_show_the_first(address):
    shown = threading.Event()
    claim(address, "AF_UNIX", shown.set, announce=True)
    assert claim(address, "AF_UNIX", lambda: None, announce=False) is False
    assert not shown.wait(timeout=0.5)


def test_with_no_first_instance_startup_proceeds(address):
    assert claim(address, "AF_UNIX", lambda: None, announce=True) is True


def test_a_silent_client_does_not_block_later_shows(address):
    shown = threading.Event()
    assert claim(address, "AF_UNIX", shown.set, announce=True) is True
    silent = socket.socket(socket.AF_UNIX)
    silent.connect(address)
    try:
        assert claim(address, "AF_UNIX", lambda: None, announce=True) is False
        assert shown.wait(timeout=5), "a client that sent nothing blocked the listener"
    finally:
        silent.close()


def test_a_path_that_cannot_be_listened_on_still_runs_the_app():
    sock_dir = tempfile.mkdtemp(dir="/tmp", prefix="omelet_sock_")
    try:
        address = f"{sock_dir}/stale.sock"
        with open(address, "w") as f:
            f.write("stale")
        assert claim(address, "AF_UNIX", lambda: None, announce=True) is True
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)


def test_the_lima_provider_leaves_single_instance_to_launchservices():
    from pathlib import Path
    from host.providers.lima import LimaProvider
    provider = LimaProvider(config=Path("/tmp/omelet.yaml"), runner=lambda a: None)
    assert provider.single_instance(lambda: None, announce=True) is True


def test_an_announcing_second_instance_lets_the_first_take_the_foreground(address):
    shown, allowed = threading.Event(), []
    claim(address, "AF_UNIX", shown.set, announce=True)
    assert claim(address, "AF_UNIX", lambda: None, announce=True,
                 before_show=lambda: allowed.append(shown.is_set())) is False
    assert allowed == [False]
    assert shown.wait(timeout=5)


def test_a_background_second_instance_does_not_hand_over_the_foreground(address):
    allowed = []
    claim(address, "AF_UNIX", lambda: None, announce=True)
    claim(address, "AF_UNIX", lambda: None, announce=False,
          before_show=lambda: allowed.append(True))
    assert allowed == []


def test_the_wsl_pipe_name_survives_an_unreadable_user_name(monkeypatch):
    import getpass
    from host.providers import instance
    from host.providers.wsl2 import Wsl2Provider

    def no_user():
        raise OSError("no USERNAME")

    seen = {}

    def fake_claim(address, family, on_show, *, announce, before_show=None):
        seen["address"] = address
        return True

    monkeypatch.setattr(getpass, "getuser", no_user)
    monkeypatch.setattr(instance, "claim", fake_claim)
    assert Wsl2Provider(runner=lambda a: None).single_instance(lambda: None, announce=True)
    assert seen["address"].startswith(r"\\.\pipe\omelet-")
