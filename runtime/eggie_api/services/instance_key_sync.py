from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from ..infra.cloud import CloudError, CloudUnavailable
from .account import NotSignedIn

log = logging.getLogger("eggie.api")
NAME_LIMIT = 64
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _line(key: dict) -> str:
    public_key = key["public_key"]
    if not isinstance(public_key, str) or not public_key.strip():
        raise TypeError("a key has no public_key")
    name = _CONTROL.sub("", str(key.get("name") or "")).strip()[:NAME_LIMIT]
    return f"{public_key.strip()} {name}" if name else public_key.strip()


class InstanceKeySync:
    """Keeps `<dir>/desired.json` equal to the keys the service approved for
    this server; a root unit installs them for sshd."""

    def __init__(self, account, cloud, directory: Path, *, clock=time.time,
                 every: float = 6 * 3600):
        self._account = account
        self._cloud = cloud
        self._file = Path(directory) / "desired.json"
        self._clock = clock
        self._every = every
        self._owed = threading.Event()
        self._synced_at: float | None = None

    def wake(self) -> None:
        self._owed.set()

    def run_pass(self) -> None:
        due = (self._synced_at is None or self._owed.is_set()
               or self._clock() - self._synced_at >= self._every)
        if due:
            self.sync()

    def sync(self) -> None:
        # Cleared first: a ping during the sync owes another; a failure re-owes it.
        self._owed.clear()
        try:
            out = self._account.authed(self._cloud.own_ssh_keys)
            text = json.dumps({"login": out["login_name"],
                               "keys": [_line(k) for k in out["keys"]]})
        except NotSignedIn:
            return
        except (CloudError, CloudUnavailable) as e:
            log.warning("could not fetch the SSH keys: %s", e)
            self._owed.set()
            return
        except (KeyError, TypeError, AttributeError) as e:
            log.warning("the SSH key list was not understood: %s", e)
            self._owed.set()
            return
        try:
            self._write(text)
        except OSError as e:
            log.warning("could not write the SSH keys: %s", e)
            self._owed.set()
            return
        self._synced_at = self._clock()

    def _write(self, text: str) -> None:
        try:
            if self._file.read_text() == text:
                return
        except OSError:
            pass
        self._file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._file.parent, prefix=".desired-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self._file)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
