from __future__ import annotations

import os
import threading

from host.providers.instance import claim


def test_the_first_instance_claims_and_a_second_is_turned_away(tmp_path):
    # Unix sockets have a path limit (~108 chars). pytest's tmp_path is too long,
    # so use a shorter path in /tmp. Each test gets a unique socket.
    address = f"/tmp/omelet_claim_a_{id(tmp_path)}.sock"
    # Clean up any stale socket file
    try:
        os.remove(address)
    except FileNotFoundError:
        pass

    shown = threading.Event()
    assert claim(address, "AF_UNIX", shown.set, announce=True) is True
    assert claim(address, "AF_UNIX", lambda: None, announce=True) is False
    assert shown.wait(timeout=5), "the first instance was never asked to show"


def test_a_background_second_instance_does_not_show_the_first(tmp_path):
    address = f"/tmp/omelet_claim_b_{id(tmp_path)}.sock"
    try:
        os.remove(address)
    except FileNotFoundError:
        pass

    shown = threading.Event()
    claim(address, "AF_UNIX", shown.set, announce=True)
    assert claim(address, "AF_UNIX", lambda: None, announce=False) is False
    assert not shown.wait(timeout=0.5)


def test_with_no_first_instance_startup_proceeds(tmp_path):
    address = f"/tmp/omelet_claim_c_{id(tmp_path)}.sock"
    try:
        os.remove(address)
    except FileNotFoundError:
        pass

    assert claim(address, "AF_UNIX", lambda: None, announce=True) is True


def test_the_lima_provider_leaves_single_instance_to_launchservices():
    from pathlib import Path
    from host.providers.lima import LimaProvider
    provider = LimaProvider(config=Path("/tmp/omelet.yaml"), runner=lambda a: None)
    assert provider.single_instance(lambda: None, announce=True) is True
