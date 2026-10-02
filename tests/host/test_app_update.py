"""Choosing a newer desktop app from GitHub's releases listing."""
import hashlib
import json

from host.core import app_update
from host.core.app_update import AppRelease, check, pick_release


def _asset(name):
    return {"name": name, "browser_download_url": f"https://dl.invalid/{name}"}


def _release(tag, *names, draft=False, prerelease=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
            "assets": [_asset(n) for n in names]}


def _exe(version):
    return f"OmeletSetup-{version}.exe"


def test_the_highest_app_release_above_this_one_wins_by_number_not_text():
    releases = [_release("app-v0.9.0", _exe("0.9.0"), "SHA256SUMS"),
                _release("app-v0.10.0", _exe("0.10.0"), "SHA256SUMS")]
    version, installer, _ = pick_release(releases, current="0.1.0", asset_name=_exe)
    assert version == "0.10.0"
    assert installer["name"] == "OmeletSetup-0.10.0.exe"


def test_runtime_tags_drafts_and_pre_releases_are_ignored():
    releases = [_release("runtime-v9.0.0", _exe("9.0.0"), "SHA256SUMS"),
                _release("app-v2.0.0", _exe("2.0.0"), "SHA256SUMS", draft=True),
                _release("app-v3.0.0", _exe("3.0.0"), "SHA256SUMS", prerelease=True),
                _release("app-v4.0.0-rc1", _exe("4.0.0-rc1"), "SHA256SUMS")]
    assert pick_release(releases, current="0.1.0", asset_name=_exe) is None


def test_this_version_or_older_is_no_update():
    releases = [_release("app-v0.1.0", _exe("0.1.0"), "SHA256SUMS")]
    assert pick_release(releases, current="0.1.0", asset_name=_exe) is None


def test_a_release_without_this_machines_installer_or_checksums_is_skipped():
    releases = [_release("app-v0.3.0", "OmeletSetup-0.3.0-arm64.pkg", "SHA256SUMS"),
                _release("app-v0.2.0", _exe("0.2.0")),
                _release("app-v0.1.5", _exe("0.1.5"), "SHA256SUMS")]
    version, _, _ = pick_release(releases, current="0.1.0", asset_name=_exe)
    assert version == "0.1.5"


def test_check_returns_the_installer_and_its_published_digest():
    digest = hashlib.sha256(b"x").hexdigest()
    listing = json.dumps([_release("app-v0.2.0", _exe("0.2.0"), "SHA256SUMS")]).encode()
    sums = f"{'0' * 64}  OmeletSetup-0.2.0-arm64.pkg\n{digest}  OmeletSetup-0.2.0.exe\n".encode()
    pages = {app_update.RELEASES_URL: listing, "https://dl.invalid/SHA256SUMS": sums}
    assert check(current="0.1.0", asset_name=_exe, fetch=pages.__getitem__) == \
        AppRelease("0.2.0", "https://dl.invalid/OmeletSetup-0.2.0.exe", digest)


def test_a_checksum_file_not_naming_the_installer_is_no_update():
    listing = json.dumps([_release("app-v0.2.0", _exe("0.2.0"), "SHA256SUMS")]).encode()
    pages = {app_update.RELEASES_URL: listing, "https://dl.invalid/SHA256SUMS": b""}
    assert check(current="0.1.0", asset_name=_exe, fetch=pages.__getitem__) is None


def test_an_unreachable_github_is_no_update():
    def offline(url):
        raise OSError("network unreachable")
    assert check(current="0.1.0", asset_name=_exe, fetch=offline) is None


def test_a_listing_that_is_not_a_list_is_no_update():
    assert check(current="0.1.0", asset_name=_exe,
                 fetch=lambda url: b'{"message": "API rate limit exceeded"}') is None
