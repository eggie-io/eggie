import sys

import host.providers as providers
from host.providers.wsl2 import Wsl2Provider
from host.providers.lima import LimaProvider


def test_factory_returns_wsl2_on_windows(monkeypatch):
    monkeypatch.setattr(providers.sys, "platform", "win32")
    assert isinstance(providers.get_provider(), Wsl2Provider)


def test_factory_returns_lima_on_macos(monkeypatch):
    monkeypatch.setattr(providers.sys, "platform", "darwin")
    assert isinstance(providers.get_provider(), LimaProvider)


def test_the_mac_provider_is_handed_a_resolved_limactl(monkeypatch):
    # The factory is where the host platform is resolved, so it is also where
    # "which limactl, given that this process may have no useful PATH" belongs.
    # The stub records its kwargs rather than ignoring them: a find_limactl()
    # call with no `managed` argument would keep this test green forever
    # while the shipped app quietly stopped using the Lima setup installed.
    from host.providers.lima import default_data_root
    from host.providers.lima_install import managed_limactl

    monkeypatch.setattr(providers.sys, "platform", "darwin")
    seen = {}

    def fake_find_limactl(**kwargs):
        seen.update(kwargs)
        return "/opt/homebrew/bin/limactl"

    monkeypatch.setattr(providers, "find_limactl", fake_find_limactl)
    provider = providers.get_provider()
    assert provider.limactl == "/opt/homebrew/bin/limactl"
    root = default_data_root()
    assert seen == {"managed": managed_limactl(root)}
    assert provider.data_root == root


def test_the_lima_data_root_is_the_parent_of_the_install_dir(monkeypatch):
    # One literal, in lima.py. Two would drift, and the symptom would be a
    # limactl setup installed somewhere the provider never looks.
    from host.providers import default_install_dir
    from host.providers.lima import default_data_root
    monkeypatch.setattr(sys, "platform", "darwin")
    assert default_install_dir().parent == default_data_root()


def test_factory_rejects_unsupported(monkeypatch):
    monkeypatch.setattr(providers.sys, "platform", "linux")
    try:
        providers.get_provider()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "linux" in str(e).lower()


def test_both_providers_satisfy_the_vm_provider_protocol():
    # Catches a Protocol member added without updating the implementations.
    from host.core.provider import VmProvider
    assert isinstance(Wsl2Provider(), VmProvider)
    assert isinstance(LimaProvider(), VmProvider)


def test_the_desktop_follows_the_same_platform_as_the_provider(monkeypatch):
    from host.providers.desktop_mac import MacDesktop
    from host.providers.desktop_win import WindowsDesktop

    monkeypatch.setattr(providers.sys, "platform", "win32")
    assert isinstance(providers.get_desktop(), WindowsDesktop)
    monkeypatch.setattr(providers.sys, "platform", "darwin")
    assert isinstance(providers.get_desktop(), MacDesktop)
