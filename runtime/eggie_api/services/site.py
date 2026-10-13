from __future__ import annotations

import logging

from ..infra.cloud import CloudError, CloudUnavailable
from .account import NotSignedIn
from .sessions import Sessions

log = logging.getLogger("eggie.site")


class SiteSignIn:
    """Trades a handoff code for a browser session. Codes minted here (the
    desktop, a signed-in page) are checked locally. On a site, a code the
    web app minted is checked with the service, which knows this instance's
    owner; the instance never sees who that is."""

    def __init__(self, sessions: Sessions, account, cloud, *, remote: bool):
        self._sessions = sessions
        self._account = account
        self._cloud = cloud
        self._remote = remote

    def redeem(self, code: str) -> str | None:
        session_id = self._sessions.redeem(code)
        if session_id is not None or not self._remote:
            return session_id
        try:
            self._account.authed(
                lambda token: self._cloud.redeem_instance_handoff(token, code))
        except NotSignedIn:
            log.warning("a sign-in link arrived but this server is not signed in")
            return None
        except (CloudError, CloudUnavailable) as e:
            log.info("the service did not accept a sign-in link: %s", e)
            return None
        return self._sessions.start()
