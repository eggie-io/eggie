from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from ..infra.cloud import CloudError, CloudUnavailable
from .account import NotSignedIn

log = logging.getLogger("eggie.certificate")
CHECK_EVERY = 24 * 3600


def _epoch(value: str) -> float:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _write(path: Path, text: str, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)


class Certificate:
    """The site's TLS certificate. The service issues and renews it; this
    keeps the files Traefik reads current, as one pass of the sync loop.

    Each pair lives in `<dir>/<not_after>/` and `<dir>/current` links to the
    live one. Traefik watches `<dir>` and reloads on every event there:
    renaming key and certificate one after the other would have it load a
    mismatched pair. Staging a directory raises events too, but swapping the
    link is the only one that changes which pair Traefik loads, so it is
    the last step of an install."""

    def __init__(self, directory: Path, account, cloud, *, names: tuple[str, ...],
                 clock=time.time):
        self._dir = Path(directory)
        self._account = account
        self._cloud = cloud
        self._names = set(names)
        self._clock = clock
        self._checked_at: float | None = None

    @property
    def _current(self) -> Path:
        return self._dir / "current"

    @property
    def cert_path(self) -> Path:
        return self._current / "site.crt"

    @property
    def key_path(self) -> Path:
        return self._current / "site.key"

    def installed_not_after(self) -> float | None:
        try:
            return float(int(os.readlink(self._current)))
        except (OSError, ValueError):
            return None

    def refresh(self) -> None:
        """Fetch when nothing is installed, else once a day. A failure is
        retried on the next pass: the daily spacing starts only after an
        answer from the service."""
        if not self._account.signed_in:
            return
        now = self._clock()
        have = self.cert_path.exists() and self.key_path.exists()
        if have and self._checked_at is not None and now - self._checked_at < CHECK_EVERY:
            return
        try:
            out = self._account.authed(self._cloud.instance_certificate)
        except NotSignedIn:
            return
        except (CloudError, CloudUnavailable) as e:
            log.warning("could not fetch the site certificate: %s", e)
            return
        try:
            self._install(out, have)
            self._checked_at = now
        except (KeyError, TypeError, ValueError, OSError) as e:
            log.warning("the service's certificate was not installed: %s", e)

    def _drop_old_pairs(self) -> None:
        # Before staging, never after the swap: a removal right after it
        # would land while Traefik may still be reloading.
        try:
            live = os.readlink(self._current)
        except OSError:
            live = None
        if not self._dir.is_dir():
            return
        for entry in self._dir.iterdir():
            if entry.name != live and entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)

    def _install(self, out: dict, have: bool) -> None:
        certificate, private_key = out["certificate"], out["private_key"]
        for value in (certificate, private_key, out["not_after"]):
            if not isinstance(value, str) or not value:
                raise TypeError("the payload is incomplete")
        missing = self._names - set(out["names"])
        if missing:
            raise ValueError(f"it does not cover {sorted(missing)}")
        stamp = str(int(_epoch(out["not_after"])))
        current = self.installed_not_after()
        self._drop_old_pairs()
        # Compared as the whole-second stamp: a later expiry inside the same
        # second would otherwise delete the live directory to rewrite it.
        if have and current is not None and int(stamp) <= current:
            return
        # No .yml/.yaml/.toml suffix on anything staged here: Traefik would
        # parse it as dynamic config.
        staging = self._dir / f"{stamp}.tmp"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        _write(staging / "site.key", private_key, 0o600)
        _write(staging / "site.crt", certificate, 0o644)
        shutil.rmtree(self._dir / stamp, ignore_errors=True)
        os.replace(staging, self._dir / stamp)
        link = self._dir / "current.tmp"
        link.unlink(missing_ok=True)
        os.symlink(stamp, link)
        os.replace(link, self._current)
        log.info("site certificate installed, valid until %s", out["not_after"])
