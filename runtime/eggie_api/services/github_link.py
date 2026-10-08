from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from ..errors import Conflict, Unavailable, Upstream
from ..infra.github import GitHubError, GitHubUnavailable, identity_from
from ..infra.repos.github import GitHubRepo

SETUP_TIMEOUT = 30.0
CHECK_EVERY = 300.0
SLOW_DOWN_STEP = 5.0
_REFUSED = {"access_denied", "expired_token"}

log = logging.getLogger("eggie.github")


def _github_down() -> Unavailable:
    return Unavailable("github_unavailable", "GitHub can't be reached. Check the "
                       "internet connection and try again.")


def _reconnect() -> Conflict:
    return Conflict("github_reconnect", "GitHub stopped accepting Eggie's "
                    "access. Reconnect GitHub and try again.")


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class NotConnected(Conflict):
    def __init__(self):
        super().__init__("github_not_connected", "Connect GitHub first.")


def _daemon(fn) -> None:
    threading.Thread(target=fn, name="eggie-github", daemon=True).start()


def _write(path: Path, text: str, mode: int) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), mode)
        f.write(text)
    os.replace(tmp, path)


def read_applied(directory: Path) -> dict | None:
    try:
        data = json.loads((directory / "applied.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def setup_state(row: dict, applied: dict | None, now: float) -> tuple[str, str | None]:
    if applied is not None and applied.get("generation") == row["generation"]:
        if applied.get("ok"):
            return "ready", None
        return "failed", applied.get("error") or "setup_failed"
    if now - (row["desired_at"] or 0) < SETUP_TIMEOUT:
        return "applying", None
    # install.sh runs the apply script once, so a VM that can apply always
    # has applied.json; none at all means the runtime predates this feature.
    if applied is None:
        return "runtime_outdated", None
    return "failed", "setup_timeout"


class GitHubLink:
    def __init__(self, github_repo: GitHubRepo, github, *, client_id: str, directory: Path,
                 clock=time.time, sleep=time.sleep, spawn=_daemon):
        self._repo = github_repo
        self._github = github
        self._client_id = client_id
        self._dir = Path(directory)
        self._clock = clock
        self._sleep = sleep
        self._spawn = spawn
        self._lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._polling = False
        # Memory only: the device code plus the public client_id is enough to
        # claim the token.
        self._pending: dict | None = None

    @property
    def _token_path(self) -> Path:
        return self._dir / "token"

    def token(self) -> str:
        with self._lock:
            row = self._repo.get()
            if not row["login"] or row["needs_reconnect"]:
                raise NotConnected()
            try:
                return self._token_path.read_text().strip()
            except OSError:
                raise NotConnected() from None

    def require_token(self) -> str:
        try:
            return self.token()
        except NotConnected:
            if self.status()["state"] == "needs_reconnect":
                raise _reconnect() from None
            raise

    def repos(self, page: int) -> dict:
        token = self.require_token()
        try:
            repos, more = self._github.repos(token, max(page, 1))
        except GitHubUnavailable:
            raise _github_down() from None
        except GitHubError as e:
            if e.code == "bad_credentials":
                self.mark_bad_if_current(token)
                raise _reconnect() from None
            raise Upstream("github_error", "GitHub refused the repository list: "
                           f"{e.message or e.code}") from None
        return {"has_more": more, "repos": [
            {"full_name": r["full_name"], "private": bool(r.get("private")),
             "description": r.get("description"),
             "updated_at": _epoch(r.get("updated_at"))} for r in repos]}

    def status(self) -> dict:
        with self._lock:
            if self._pending:
                return {"state": "pending", "user_code": self._pending["user_code"],
                        "url": self._pending["url"],
                        "expires_at": self._pending["expires_at"]}
            row = self._repo.get()
            if row["needs_reconnect"]:
                return {"state": "needs_reconnect", "login": row["login"]}
            if row["login"] and self._token_path.exists():
                setup, error = setup_state(row, read_applied(self._dir), self._clock())
                return {"state": "connected", "login": row["login"],
                        "name": row["name"], "email": row["email"],
                        "setup": setup, "setup_error": error}
            return {"state": "disconnected", "error": row["last_error"]}

    def connect(self) -> dict:
        with self._lock:
            if self._pending and self._pending["expires_at"] > self._clock():
                return self.status()
            if self.status()["state"] == "connected":
                return self.status()
        try:
            out = self._github.device_code(self._client_id)
        except GitHubUnavailable:
            raise _github_down() from None
        except GitHubError as e:
            raise Upstream("github_error", "GitHub would not start a sign-in: "
                           f"{e.message or e.code}") from None
        with self._lock:
            self._pending = {"device_code": out["device_code"],
                             "user_code": out["user_code"],
                             "url": out["verification_uri"],
                             "expires_at": self._clock() + out["expires_in"],
                             "interval": float(out.get("interval") or 5)}
            self._repo.update(last_error=None)
        self._ensure_poller()
        return self.status()

    def _ensure_poller(self) -> None:
        with self._poll_lock:
            if self._polling:
                return
            self._polling = True
        self._spawn(self._run_poller)

    def _run_poller(self) -> None:
        while True:
            try:
                wait = self.poll_once()
            except Exception:
                log.exception("GitHub poll failed")
                wait = 5.0
            if wait is not None:
                self._sleep(wait)
                continue
            with self._poll_lock:
                if not self._pending:
                    self._polling = False
                    return

    def poll_once(self) -> float | None:
        with self._lock:
            pending = self._pending
            if not pending:
                return None
            if self._clock() >= pending["expires_at"]:
                self._end(pending, "expired_token")
                return None
        try:
            out = self._github.device_token(self._client_id, pending["device_code"])
            error = None
        except GitHubUnavailable:
            return pending["interval"]
        except GitHubError as e:
            out, error = None, e

        with self._lock:
            if self._pending is not pending:
                return self._pending["interval"] if self._pending else None
            if error is not None:
                if error.code == "authorization_pending":
                    return pending["interval"]
                if error.code == "slow_down":
                    pending["interval"] = max(pending["interval"] + SLOW_DOWN_STEP,
                                              error.interval or 0)
                    return pending["interval"]
                self._end(pending, error.code if error.code in _REFUSED
                          else "github_error")
                return None
        token = out["access_token"]
        try:
            identity = identity_from(self._github.user(token))
        except (GitHubError, GitHubUnavailable):
            with self._lock:
                if self._pending is pending:
                    self._end(pending, "github_error")
            return None
        with self._lock:
            if self._pending is not pending:
                return None
            self._pending = None
            self._dir.mkdir(parents=True, exist_ok=True)
            _write(self._token_path, token, 0o600)
            self._repo.update(needs_reconnect=0, last_error=None,
                                      checked_at=self._clock(), **identity)
            self._write_desired("connected")
        return None

    def _end(self, pending: dict, error: str) -> None:
        if self._pending is pending:
            self._pending = None
            self._repo.update(last_error=error)

    def _write_desired(self, state: str) -> None:
        row = self._repo.get()
        applied = read_applied(self._dir) or {}
        applied_gen = applied.get("generation")
        # A lost state.db restarts at 0; the next generation must still pass
        # whatever the script last applied, or "ready" would match stale work.
        generation = max(row["generation"],
                         applied_gen if isinstance(applied_gen, int) else 0) + 1
        doc = {"generation": generation, "state": state}
        if state == "connected":
            doc.update(login=row["login"], name=row["name"], email=row["email"])
        self._dir.mkdir(parents=True, exist_ok=True)
        _write(self._dir / "desired.json", json.dumps(doc), 0o640)
        self._repo.update(generation=generation, desired_at=self._clock())

    def disconnect(self) -> dict:
        with self._lock:
            self._pending = None
            self._token_path.unlink(missing_ok=True)
            self._repo.update(login=None, gh_id=None, name=None, email=None,
                                      needs_reconnect=0, last_error=None,
                                      checked_at=None)
            self._write_desired("disconnected")
            return self.status()

    def reapply(self) -> dict:
        try:
            return self._reapply()
        except GitHubUnavailable:
            raise _github_down() from None
        except GitHubError as e:
            raise Upstream("github_error", "GitHub would not confirm the "
                           f"connection: {e.message or e.code}") from None

    def _reapply(self) -> dict:
        token = self.token()
        try:
            identity = identity_from(self._github.user(token))
            error = None
        except GitHubError as e:
            identity, error = None, e

        with self._lock:
            # A disconnect/reconnect racing the network call above must win:
            # a reply about a token that is no longer the stored one must
            # neither write a stale identity nor mark a fresh one bad.
            if not self._is_current(token):
                raise NotConnected()
            if error is not None:
                if error.code == "bad_credentials":
                    self.mark_bad_credentials()
                    raise NotConnected() from None
                raise error
            self._repo.update(**identity)
            self._write_desired("connected")
            return self.status()

    def _is_current(self, token: str) -> bool:
        row = self._repo.get()
        if not row["login"] or row["needs_reconnect"]:
            return False
        try:
            return self._token_path.read_text().strip() == token
        except OSError:
            return False

    def mark_bad_credentials(self) -> None:
        # No new generation: the accounts keep the dead token until the user
        # reconnects or disconnects, and either writes a fresh desired state.
        with self._lock:
            self._token_path.unlink(missing_ok=True)
            self._repo.update(needs_reconnect=1)

    def check_token(self) -> None:
        with self._lock:
            row = self._repo.get()
            if (self.status()["state"] != "connected"
                    or self._clock() - (row["checked_at"] or 0) < CHECK_EVERY):
                return
            self._repo.update(checked_at=self._clock())
            token = self._token_path.read_text().strip()
        self.confirm_bad(token)

    def confirm_bad(self, token: str) -> None:
        # A git 403 covers SSO/permission refusals on a token that still
        # works, not only a revoked one, so re-check with GitHub before
        # marking it bad.
        try:
            self._github.user(token)
        except GitHubError as e:
            if e.code == "bad_credentials":
                self.mark_bad_if_current(token)
        except GitHubUnavailable:
            pass

    def mark_bad_if_current(self, token: str) -> None:
        # A disconnect/reconnect that landed while `token` was out on a
        # GitHub call must win over marking that (possibly stale) token bad.
        with self._lock:
            if self._is_current(token):
                self.mark_bad_credentials()
