from __future__ import annotations

import hashlib
import secrets
import threading
import time

COOKIE = "eggie_session"
SESSION_TTL = 7 * 24 * 3600
HANDOFF_TTL = 60
# Slide the row (and, in the API layer, re-issue the cookie) only once
# under an hour of the last extension has passed, so an active browser
# polling every few seconds doesn't write to sqlite on every request.
SLIDE_WINDOW = SESSION_TTL - 3600


def _hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


class Verdict(str):
    """`check()`'s result. Compares equal to the plain verdict string like
    before, but also carries whether this call slid the expiry forward, which
    the API layer needs to decide whether to re-issue the cookie."""

    def __new__(cls, value: str, *, extended: bool = False):
        self = str.__new__(cls, value)
        self.extended = extended
        return self


class Sessions:
    """Browser sign-in. Handoff codes live in memory only: one that outlives an
    API restart would be a bearer credential sitting in the database."""

    def __init__(self, state, *, clock=time.time):
        self._state = state
        self._clock = clock
        self._codes: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue_handoff(self) -> str:
        code = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._codes = {c: t for c, t in self._codes.items() if t > now}
            self._codes[code] = now + HANDOFF_TTL
        return code

    def redeem(self, code: str) -> str | None:
        with self._lock:
            expires = self._codes.pop(code, None)
        if expires is None or expires <= self._clock():
            return None
        session_id = secrets.token_urlsafe(32)
        self._state.add_session(_hash(session_id), self._clock() + SESSION_TTL)
        return session_id

    def check(self, session_id: str | None) -> Verdict:
        if not session_id:
            return Verdict("missing")
        key = _hash(session_id)
        row = self._state.get_session(key)
        if row is None:
            return Verdict("missing")
        now = self._clock()
        if row["expires_at"] <= now:
            self._state.remove_session(key)
            return Verdict("expired")
        if row["expires_at"] - now < SLIDE_WINDOW:
            self._state.set_session_expiry(key, now + SESSION_TTL)
            return Verdict("ok", extended=True)
        return Verdict("ok")

    def end(self, session_id: str | None) -> None:
        if session_id:
            self._state.remove_session(_hash(session_id))
