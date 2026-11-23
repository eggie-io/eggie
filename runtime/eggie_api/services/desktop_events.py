from __future__ import annotations

import logging
import threading

from ..domain import desktop_events
from ..infra.cloud import CloudError, CloudUnavailable

log = logging.getLogger("eggie.api")


def _on_thread(fn) -> None:
    threading.Thread(target=fn, name="desktop-event", daemon=True).start()


class DesktopEvents:
    def __init__(self, cloud, *, start=_on_thread):
        self._cloud = cloud
        self._start = start

    def relay(self, body: object) -> None:
        parsed = desktop_events.parse(body)
        self._start(lambda: self._send(parsed))

    def _send(self, parsed: dict) -> None:
        try:
            self._cloud.desktop_event(parsed)
        except (CloudError, CloudUnavailable, OSError) as e:
            log.warning("desktop event %s not delivered: %s", parsed["event"], e)
