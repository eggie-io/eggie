from __future__ import annotations

import logging
import time

from ..domain.push import safe_return_path
from ..infra.cloud import CloudError, CloudUnavailable
from .account import NotSignedIn
from .ratelimit import Window
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
        self._window = Window(REMOTE_PER_MINUTE, _WINDOW, clock, what="sign-in links")

    def redeem(self, code: str) -> tuple[str, str | None] | None:
        """(session id, page to land on); local codes carry no page."""
        session_id = self._sessions.redeem(code)
        if session_id is not None:
            return session_id, None
        if not self._remote or self._sessions.is_local(code):
            return None
        if not self._window.take():
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
        return self._sessions.start(), safe_return_path(out.get("return_path"))
