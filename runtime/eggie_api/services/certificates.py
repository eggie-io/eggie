from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from collections.abc import Collection
from pathlib import Path

from ..domain.edge import Pair
from ..infra.cloud import CloudError, CloudUnavailable
from .account import NotSignedIn

log = logging.getLogger("eggie.certificates")
CHECK_EVERY = 24 * 3600
# An id names a directory and lands in Traefik's file.
_ID = re.compile(r"[A-Za-z0-9_-]+")


def _write(path: Path, text: str, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _text(out: dict, key: str) -> str:
    value = out[key]
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} is missing")
    return value


def _names(value) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(n, str) for n in value):
        raise TypeError("names is not a list of names")
    return tuple(value)


class Certificates:
    """The certificates the service holds for this server, kept on disk as
    `<dir>/certs/<id>/{cert.pem,key.pem,meta.json}`. A renewal arrives under
    a new id and the old id leaves the list, so a pair is never rewritten.
    A pair the list dropped keeps serving its names until every listed pair
    that shares one of them is installed."""

    def __init__(self, directory: Path, account, cloud, *, clock=time.time):
        self._certs = Path(directory) / "certs"
        self._account = account
        self._cloud = cloud
        self._clock = clock
        self._checked_at: float | None = None
        self._listed: dict[str, tuple[str, ...]] | None = None

    def _on_disk(self) -> list[Pair]:
        pairs = []
        if not self._certs.is_dir():
            return pairs
        for entry in sorted(self._certs.iterdir()):
            if entry.name.endswith(".tmp"):
                continue
            try:
                meta = json.loads((entry / "meta.json").read_text())
                kind, names = meta["kind"], _names(meta.get("names", []))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            cert, key = entry / "cert.pem", entry / "key.pem"
            if cert.is_file() and key.is_file():
                pairs.append(Pair(entry.name, kind, str(cert), str(key), names))
        return pairs

    def installed(self) -> list[Pair]:
        pairs = self._on_disk()
        # Before the first list everything on disk counts.
        if self._listed is None:
            return pairs
        have = {p.id for p in pairs}
        awaited = {n for cid, names in self._listed.items() if cid not in have for n in names}
        return [p for p in pairs if p.id in self._listed or awaited.intersection(p.names)]

    def refresh(self, wanted: Collection[str] = ()) -> None:
        if not self._account.signed_in:
            return
        self._remove_unlisted()
        have = {p.id for p in self._on_disk()}
        now = self._clock()
        due = (not have or not set(wanted) <= have or self._checked_at is None
               or now - self._checked_at >= CHECK_EVERY)
        if due:
            try:
                out = self._account.authed(self._cloud.instance_certificates)
                listed = {_text(c, "id"): _names(c["names"]) for c in out["certificates"]}
                for cid in [cid for cid in listed if not _ID.fullmatch(cid)]:
                    log.warning("skipping certificate %r: not a plain id", cid)
                    del listed[cid]
            except NotSignedIn:
                return
            except (CloudError, CloudUnavailable) as e:
                log.warning("could not list the certificates: %s", e)
                return
            except (KeyError, TypeError) as e:
                log.warning("the certificate list was not understood: %s", e)
                return
            self._listed = listed
            self._checked_at = now
        for cid in self._listed or {}:
            if cid not in have and not self._fetch(cid):
                return

    def recheck(self) -> None:
        """The next refresh lists again, so a removed domain's pair goes promptly."""
        self._checked_at = None

    def _remove_unlisted(self) -> None:
        # Only after a list: from disk alone nothing is known to be stale.
        if self._listed is None or not self._certs.is_dir():
            return
        keep = set(self._listed) | {p.id for p in self.installed()}
        for entry in self._certs.iterdir():
            if entry.name not in keep and entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)

    def _fetch(self, cid: str) -> bool:
        """False only when the account signed out: the pass should end."""
        try:
            out = self._account.authed(lambda t: self._cloud.instance_certificate(t, cid))
        except NotSignedIn:
            return False
        except (CloudError, CloudUnavailable) as e:
            log.warning("could not fetch certificate %s: %s", cid, e)
            return True
        try:
            self._install(cid, out)
        except (KeyError, TypeError, ValueError, OSError) as e:
            log.warning("certificate %s was not installed: %s", cid, e)
        return True

    def _install(self, cid: str, out: dict) -> None:
        if out.get("id") != cid or not _ID.fullmatch(cid):
            raise ValueError("the id does not match")
        certificate, private_key = _text(out, "certificate"), _text(out, "private_key")
        meta = {"id": cid, "kind": _text(out, "kind"), "names": list(_names(out["names"])),
                "not_after": _text(out, "not_after")}
        staging = self._certs / f"{cid}.tmp"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            _write(staging / "key.pem", private_key, 0o600)
            _write(staging / "cert.pem", certificate, 0o644)
            _write(staging / "meta.json", json.dumps(meta), 0o644)
            shutil.rmtree(self._certs / cid, ignore_errors=True)
            os.replace(staging, self._certs / cid)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        log.info("certificate %s installed, valid until %s", cid, meta["not_after"])
