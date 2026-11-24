"""The macOS desktop surface: Login Items, and what LaunchServices already does."""
from host.providers.desktop_mac import MacDesktop


class FakeLoginItems:
    def __init__(self, enabled=False, fail=None):
        self._enabled, self._fail, self.calls = enabled, fail, []

    def enabled(self):
        return self._enabled

    def register(self):
        self.calls.append("register")
        if self._fail:
            raise self._fail
        self._enabled = True

    def unregister(self):
        self.calls.append("unregister")
        self._enabled = False


def _with_login_items(items):
    return MacDesktop(login_items=items)


def test_set_autostart_registers_and_unregisters_the_main_app():
    items = FakeLoginItems()
    desktop = _with_login_items(items)
    desktop.set_autostart(True, "/ignored")
    assert desktop.autostart_enabled("/ignored") is True
    desktop.set_autostart(False, "/ignored")
    assert items.calls == ["register", "unregister"]
    assert desktop.autostart_enabled("/ignored") is False


def test_a_refused_registration_reaches_the_caller():
    import pytest
    desktop = _with_login_items(FakeLoginItems(fail=RuntimeError("not approved")))
    with pytest.raises(RuntimeError, match="not approved"):
        desktop.set_autostart(True, "/ignored")


def test_single_instance_is_left_to_launchservices():
    assert MacDesktop().single_instance(lambda: None, announce=True) is True


def test_system_info_is_accepted_even_when_macos_reports_no_version(monkeypatch):
    import re

    import host.providers.desktop_mac as mod
    monkeypatch.setattr(mod.platform, "mac_ver", lambda: ("", ("", "", ""), ""))
    monkeypatch.setattr(mod.platform, "machine", lambda: "arm64")
    info = MacDesktop().system_info()
    assert info["os"] == "macos" and info["arch"] == "arm64"
    assert re.fullmatch(r"[0-9A-Za-z._-]{1,32}", info["os_version"])
