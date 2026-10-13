from __future__ import annotations

import logging
import threading
import time
from collections import deque

from ..infra.cloud import CloudError, CloudUnavailable
from .account import NotSignedIn
from .sessions import Sessions

log = logging.getLogger("eggie.site")

# The service owns single use and expiry; this only stops one instance being
# used to hammer the service with guesses.
REMOTE_PER_MINUTE = 10
_WINDOW = 60.0


class SiteSignIn:
    """Trades a handoff code for a browser session. Codes minted here (the
    desktop, a signed-in page) are checked locally. On a site, a code the
    web app minted is checked with the service, which knows this instance's
    owner; the instance never sees who that is."""

    def __init__(self, sessions: Sessions, account, cloud, *, remote: bool,
                 clock=time.time):
        self._sessions = sessions
        self._account = account
        self._cloud = cloud
        self._remote = remote
        self._clock = clock
        self._recent: deque[float] = deque()
        self._warned = False
        self._lock = threading.Lock()

    def _take_remote_slot(self) -> bool:
        now = self._clock()
        with self._lock:
            while self._recent and self._recent[0] <= now - _WINDOW:
                self._recent.popleft()
            if len(self._recent) >= REMOTE_PER_MINUTE:
                warn, self._warned = not self._warned, True
            else:
                self._recent.append(now)
                self._warned = False
                return True
        if warn:
            log.warning("more than %d sign-in links a minute; holding the rest back",
                        REMOTE_PER_MINUTE)
        return False

    def redeem(self, code: str) -> str | None:
        session_id = self._sessions.redeem(code)
        if session_id is not None or not self._remote or self._sessions.is_local(code):
            return session_id
        if not self._take_remote_slot():
            return None
        try:
            out = self._account.authed(
                lambda token: self._cloud.redeem_instance_handoff(token, code))
        except NotSignedIn:
            log.warning("a sign-in link arrived but this server is not signed in")
            return None
        except (CloudError, CloudUnavailable) as e:
            log.info("the service did not accept a sign-in link: %s", e)
            return None
        if not (isinstance(out, dict) and out.get("ok") is True):
            log.info("the service answered a sign-in link without ok: %r", out)
            return None
        return self._sessions.start()
