"""The desktop's own update: found, downloaded, verified, launched, closed."""
from __future__ import annotations

import hashlib

from host.core.app_update import AppRelease
from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi


class FakeProvider:
    def __init__(self):
        self.launched = []

    def installer_asset(self, version):
        return f"EggieSetup-{version}.exe"

    def launch_installer(self, path):
        self.launched.append(path)


def _api(tmp_path, release, *, pushed=None, quits=None, provider=None):
    return DesktopApi(provider or FakeProvider(), InstallState(tmp_path / "s.json"),
                      push=(pushed.append if pushed is not None else lambda e: None),
                      probe_fn=lambda p: Readiness(),
                      install_dir_factory=lambda: tmp_path / "eggie" / "vm",
                      app_update_fn=lambda: release,
                      quit_app=(lambda: quits.append(True)) if quits is not None else lambda: None)


def test_home_shows_an_update_only_after_one_was_found(tmp_path):
    api = _api(tmp_path, AppRelease("0.2.0", "https://dl.invalid/x.exe", "0" * 64))
    assert api.home()["app_update"] == ""
    api.check_app_update()
    assert api.home()["app_update"] == "0.2.0"


def _check_in_background(api):
    api.start_app_update_check()
    api._app_check.join(timeout=5)


def test_a_release_found_in_the_background_is_announced(tmp_path):
    # Home has usually drawn before the check returns; the push is what redraws it.
    pushed = []
    api = _api(tmp_path, AppRelease("0.2.0", "https://dl.invalid/x.exe", "0" * 64), pushed=pushed)
    _check_in_background(api)
    assert pushed == [{"kind": "app_update", "type": "available", "version": "0.2.0"}]


def test_no_release_found_in_the_background_announces_nothing(tmp_path):
    pushed = []
    api = _api(tmp_path, None, pushed=pushed)
    _check_in_background(api)
    assert pushed == []


def test_a_background_check_that_fails_stays_quiet(tmp_path):
    def failing():
        raise OSError("no network")

    pushed = []
    api = DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"), push=pushed.append,
                     probe_fn=lambda p: Readiness(), app_update_fn=failing)
    errors = []
    import threading
    previous, threading.excepthook = threading.excepthook, errors.append
    try:
        _check_in_background(api)
    finally:
        threading.excepthook = previous
    assert errors == []
    assert pushed == []
    assert api.home()["app_update"] == ""


def test_no_release_means_nothing_to_offer(tmp_path):
    api = _api(tmp_path, None)
    assert api.check_app_update()["available"] == ""
    assert api.start_app_update() == {"ok": False}


def test_the_installer_is_verified_launched_and_the_app_closes(tmp_path, monkeypatch):
    body = b"installer"
    # The cached file takes the validated asset name, never the URL's last segment.
    release = AppRelease("0.2.0", "https://dl.invalid/download?id=..%2Fevil",
                         hashlib.sha256(body).hexdigest())

    import io
    from host.core import download
    monkeypatch.setattr(download, "_default_opener",
                        lambda url, start: (io.BytesIO(body), len(body)))
    provider, pushed, quits = FakeProvider(), [], []
    api = _api(tmp_path, release, pushed=pushed, quits=quits, provider=provider)
    api.check_app_update()
    assert api.start_app_update()["version"] == "0.2.0"
    api.jobs.join(timeout=5)
    (path,) = provider.launched
    assert path.read_bytes() == body
    assert path.name == "EggieSetup-0.2.0.exe"
    assert path.parent == tmp_path / "eggie" / "cache"
    assert quits == [True]


def test_a_download_that_does_not_match_its_digest_is_never_launched(tmp_path, monkeypatch):
    import io
    from host.core import download
    monkeypatch.setattr(download, "_default_opener",
                        lambda url, start: (io.BytesIO(b"tampered"), 8))
    provider, pushed, quits = FakeProvider(), [], []
    api = _api(tmp_path, AppRelease("0.2.0", "https://dl.invalid/EggieSetup-0.2.0.exe", "0" * 64),
               pushed=pushed, quits=quits, provider=provider)
    api.check_app_update()
    api.start_app_update()
    api.jobs.join(timeout=5)
    assert provider.launched == []
    assert quits == []
    assert pushed[-1]["type"] == "crashed"


def test_the_update_quit_leaves_the_vm_running(tmp_path, monkeypatch):
    # The installer relaunches the app at once; a stop here only adds a
    # stop and a boot to every update.
    from host.core import download

    class Provider(FakeProvider):
        stops = 0
        def stop(self):
            Provider.stops += 1

    monkeypatch.setattr(download, "fetch", lambda *a, **k: tmp_path / "setup.exe")
    quits = []
    api = _api(tmp_path, AppRelease("0.2.0", "https://dl.invalid/x.exe", "0" * 64),
               quits=quits, provider=Provider())
    api.check_app_update()
    api.start_app_update()
    api.jobs.join(timeout=5)
    assert quits == [True]
    assert Provider.stops == 0
