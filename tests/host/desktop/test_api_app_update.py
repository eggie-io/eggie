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
        return f"OmeletSetup-{version}.exe"

    def launch_installer(self, path):
        self.launched.append(path)


def _api(tmp_path, release, *, pushed=None, quits=None, provider=None):
    return DesktopApi(provider or FakeProvider(), InstallState(tmp_path / "s.json"),
                      push=(pushed.append if pushed is not None else lambda e: None),
                      probe_fn=lambda p: Readiness(),
                      install_dir_factory=lambda: tmp_path / "omelet" / "vm",
                      app_update_fn=lambda: release,
                      quit_app=(lambda: quits.append(True)) if quits is not None else lambda: None)


def test_home_shows_an_update_only_after_one_was_found(tmp_path):
    api = _api(tmp_path, AppRelease("0.2.0", "https://dl.invalid/x.exe", "0" * 64))
    assert api.home()["app_update"] == ""
    api.check_app_update()
    assert api.home()["app_update"] == "0.2.0"


def test_no_release_means_nothing_to_offer(tmp_path):
    api = _api(tmp_path, None)
    assert api.check_app_update()["available"] == ""
    assert api.start_app_update() == {"ok": False}


def test_the_installer_is_verified_launched_and_the_app_closes(tmp_path, monkeypatch):
    body = b"installer"
    release = AppRelease("0.2.0", "https://dl.invalid/OmeletSetup-0.2.0.exe",
                         hashlib.sha256(body).hexdigest())

    import io
    from host.core import download
    monkeypatch.setattr(download, "_default_opener",
                        lambda url, start: (io.BytesIO(body), len(body)))
    provider, pushed, quits = FakeProvider(), [], []
    api = _api(tmp_path, release, pushed=pushed, quits=quits, provider=provider)
    api.check_app_update()
    api.start_app_update()
    api.jobs.join(timeout=5)
    (path,) = provider.launched
    assert path.read_bytes() == body
    assert path.name == "OmeletSetup-0.2.0.exe"
    assert quits == [True]


def test_a_download_that_does_not_match_its_digest_is_never_launched(tmp_path, monkeypatch):
    import io
    from host.core import download
    monkeypatch.setattr(download, "_default_opener",
                        lambda url, start: (io.BytesIO(b"tampered"), 8))
    provider, pushed, quits = FakeProvider(), [], []
    api = _api(tmp_path, AppRelease("0.2.0", "https://dl.invalid/OmeletSetup-0.2.0.exe", "0" * 64),
               pushed=pushed, quits=quits, provider=provider)
    api.check_app_update()
    api.start_app_update()
    api.jobs.join(timeout=5)
    assert provider.launched == []
    assert quits == []
    assert pushed[-1]["type"] == "crashed"
