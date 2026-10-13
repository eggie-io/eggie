from __future__ import annotations

import logging
import os
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
    # Temp name then rename: Traefik watches the directory and must never
    # read a half-written file.
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


class Certificate:
    """The site's TLS certificate. The service issues and renews it; this
    keeps the files Traefik reads current, as one pass of the sync loop."""

    def __init__(self, directory: Path, account, cloud, *, names: tuple[str, ...],
                 clock=time.time):
        self._dir = Path(directory)
        self._account = account
        self._cloud = cloud
        self._names = set(names)
        self._clock = clock
        self._checked_at: float | None = None

    @property
    def cert_path(self) -> Path:
        return self._dir / "site.crt"

    @property
    def key_path(self) -> Path:
        return self._dir / "site.key"

    @property
    def _expiry_path(self) -> Path:
        return self._dir / "site.not_after"

    def installed_not_after(self) -> float | None:
        try:
            return float(self._expiry_path.read_text())
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

    def _install(self, out: dict, have: bool) -> None:
        certificate, private_key = out["certificate"], out["private_key"]
        for value in (certificate, private_key, out["not_after"]):
            if not isinstance(value, str) or not value:
                raise TypeError("the payload is incomplete")
        missing = self._names - set(out["names"])
        if missing:
            raise ValueError(f"it does not cover {sorted(missing)}")
        not_after = _epoch(out["not_after"])
        current = self.installed_not_after()
        if have and current is not None and not_after <= current:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        _write(self.key_path, private_key, 0o600)
        _write(self.cert_path, certificate, 0o644)
        _write(self._expiry_path, repr(not_after), 0o644)
        log.info("site certificate installed, valid until %s", out["not_after"])
