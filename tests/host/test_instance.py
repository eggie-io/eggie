from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time

import pytest

from host.providers.instance import claim, SHOW, HELLO


@pytest.fixture
def sock_dir():
    """Create a temporary directory for sockets, cleaned up after the test."""
    tmpdir = tempfile.mkdtemp(dir="/tmp", prefix="omelet_sock_")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


def socket_path(sock_dir, name):
    """Return a short socket path within the fixture directory."""
    return os.path.join(sock_dir, f"{name}.sock")


def test_the_first_instance_claims_and_a_second_is_turned_away(sock_dir):
    address = socket_path(sock_dir, "claim_first")

    shown = threading.Event()
    assert claim(address, "AF_UNIX", shown.set, announce=True) is True
    time.sleep(0.05)  # Ensure listener thread has started.
    assert claim(address, "AF_UNIX", lambda: None, announce=True) is False
    assert shown.wait(timeout=5), "the first instance was never asked to show"


def test_a_background_second_instance_does_not_show_the_first(sock_dir):
    address = socket_path(sock_dir, "claim_bg")

    shown = threading.Event()
    claim(address, "AF_UNIX", shown.set, announce=True)
    time.sleep(0.05)  # Ensure listener thread has started.
    assert claim(address, "AF_UNIX", lambda: None, announce=False) is False
    assert not shown.wait(timeout=0.5)


def test_with_no_first_instance_startup_proceeds(sock_dir):
    address = socket_path(sock_dir, "claim_new")

    assert claim(address, "AF_UNIX", lambda: None, announce=True) is True


def test_listener_survives_bad_client(sock_dir):
    """Listener continues after a bad client (connects, sends nothing); next valid client is served."""
    address = socket_path(sock_dir, "bad_client")
    shown = threading.Event()

    # First instance starts listening.
    assert claim(address, "AF_UNIX", shown.set, announce=True) is True

    # A bad client connects but sends nothing, keeping the socket open.
    # Without poll() and a timeout in handle_conn(), this would block the listener forever.
    import socket
    bad_sock = socket.socket(socket.AF_UNIX)
    bad_sock.connect(address)
    # Connection open but no data sent; keep it open.

    # A valid second instance should still reach the first and trigger on_show.
    # No time.sleep() needed; listener accepts multiple connections at once.
    assert claim(address, "AF_UNIX", lambda: None, announce=True) is False
    assert shown.wait(timeout=5), "listener died or on_show was not called after bad client"

    # Clean up the bad socket.
    bad_sock.close()


def test_claim_handles_stale_socket_file(sock_dir):
    """Claim returns True when a stale non-socket file blocks listening."""
    address = socket_path(sock_dir, "stale")

    # Create a regular file where the socket would go.
    with open(address, "w") as f:
        f.write("stale")

    # claim() should handle this gracefully and return True (run normally).
    # It will try to listen, fail, then retry as a client, find nothing, then run.
    result = claim(address, "AF_UNIX", lambda: None, announce=True)
    assert result is True, "claim should return True when it cannot listen on a stale file"


def test_the_lima_provider_leaves_single_instance_to_launchservices():
    from pathlib import Path
    from host.providers.lima import LimaProvider
    provider = LimaProvider(config=Path("/tmp/omelet.yaml"), runner=lambda a: None)
    assert provider.single_instance(lambda: None, announce=True) is True
