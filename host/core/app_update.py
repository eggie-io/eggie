"""Is there a newer desktop app, and where is its installer?"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from . import constants

RELEASES_URL = constants.HOST_RELEASES_URL
_TAG = re.compile(r"host-v(\d+\.\d+\.\d+)")
_SUMS = "SHA256SUMS"


@dataclass(frozen=True)
class AppRelease:
    version: str
    url: str
    sha256: str


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(n) for n in match.groups()) if match else None


def pick_release(releases, *, current: str, asset_name: Callable[[str], str]):
    floor = parse_version(current) or (0, 0, 0)
    found = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = _TAG.fullmatch(release.get("tag_name", ""))
        if not tag or parse_version(tag[1]) <= floor:
            continue
        assets = {a.get("name"): a for a in release.get("assets", [])}
        installer, sums = assets.get(asset_name(tag[1])), assets.get(_SUMS)
        if installer and sums:
            found.append((parse_version(tag[1]), tag[1], installer, sums))
    if not found:
        return None
    _, version, installer, sums = max(found, key=lambda f: f[0])
    return version, installer, sums


def _digest_for(sums: str, name: str) -> str | None:
    for line in sums.splitlines():
        parts = line.split()
        # sha256sum marks binary mode with a leading "*" on the name.
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            return parts[0]
    return None


def _fetch(url: str) -> bytes:
    from urllib.request import Request, urlopen
    # GitHub's API refuses requests without a User-Agent.
    request = Request(url, headers={"User-Agent": f"omelet/{constants.APP_VERSION}",
                                    "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=10) as response:
        return response.read()


def check(*, current: str, asset_name: Callable[[str], str],
          fetch: Callable[[str], bytes] = _fetch) -> AppRelease | None:
    """The newest installable release above `current`, or None. Never raises:
    an update check that fails is the same as no update."""
    try:
        releases = json.loads(fetch(RELEASES_URL))
        if not isinstance(releases, list):
            return None
        picked = pick_release(releases, current=current, asset_name=asset_name)
        if picked is None:
            return None
        version, installer, sums = picked
        digest = _digest_for(fetch(sums["browser_download_url"]).decode("utf-8", "replace"),
                             installer["name"])
        if digest is None:
            return None
        return AppRelease(version, installer["browser_download_url"], digest)
    except Exception:
        return None
