import json
import logging
import time
from pathlib import Path

import pytest

from host.core.events import EventSender


@pytest.fixture(autouse=True)
def _forget_eggie_log_handlers():
    """run() and log.setup() leave handlers on the root logger; an open
    RotatingFileHandler on tmp_path would block its cleanup on Windows."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "eggie", False):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def _no_real_home(tmp_path, monkeypatch):
    """DesktopApi reads ~/.ssh, ~/.claude, ~/.codex and settings.json on every
    home(); none of these tests may see the developer's own."""
    from host.providers import default_install_dir

    home = tmp_path / "user-home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert Path.home() == home
    assert default_install_dir().is_relative_to(home)


CONTEXT = {"app_version": "0.9.1", "os": "windows", "os_version": "10.0.26100", "arch": "AMD64"}


class EventRecorder:
    """A real EventSender whose posts land in a list instead of the network."""

    def __init__(self, delay: float = 0.0):
        self.bodies = []
        self._delay = delay
        self.sender = EventSender("http://events.invalid/api", CONTEXT, post=self._post)

    def _post(self, url, body):
        if self._delay:
            time.sleep(self._delay)
        self.bodies.append(json.loads(body))

    def sent(self, *, at_least: int = 0, timeout: float = 5.0) -> list[tuple[str, dict]]:
        deadline = time.monotonic() + timeout
        while len(self.bodies) < at_least and time.monotonic() < deadline:
            time.sleep(0.01)
        self.sender.flush(timeout)
        return [(b["event"], b["properties"]) for b in self.bodies]


@pytest.fixture
def events():
    return EventRecorder()


@pytest.fixture
def slow_events():
    return EventRecorder(delay=0.2)
