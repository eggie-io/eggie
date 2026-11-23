"""What the desktop app tells the Eggie service: launches, setup runs, errors.

host/core/events.py sends; this decides when and with what.
"""
from __future__ import annotations

import getpass
import logging
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from host.core import constants
from host.core.events import EventSender, error_code, events_base, sanitize

from .settings import FIRST_LAUNCH_SENT, Settings

log = logging.getLogger(__name__)

LOGIN_SIGNAL_WAIT = 10.0


def who() -> dict[str, str]:
    """The home folder and user name every message is scrubbed of."""
    try:
        home = str(Path.home())
    except Exception:
        home = ""
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    return {"home": home, "user": user}


def make_sender(desktop, *, frozen: bool, env: Mapping[str, str]) -> EventSender:
    base = events_base(frozen=frozen, env=env)
    if base is None:
        return EventSender(None, {})
    try:
        context = {"app_version": constants.APP_VERSION, **desktop.system_info()}
    except Exception:
        # The service refuses a body without its context; send nothing instead.
        log.warning("could not read the system for events", exc_info=True)
        return EventSender(None, {})
    return EventSender(base, context)


def start_sender(desktop, *, frozen: bool, env: Mapping[str, str]) -> EventSender:
    """The sender for a desktop app process, with the crash hooks in place."""
    sender = make_sender(desktop, frozen=frozen, env=env)
    if sender.enabled:
        install_hooks(sender, **who())
    return sender


def report_launch(sender: EventSender, settings: Settings, *, background: bool,
                  late: bool, wait: Callable[[float], bool]) -> threading.Thread | None:
    """Returns the thread holding a launch for the login signal, if any."""
    if not sender.enabled:
        return None
    if not settings.get(FIRST_LAUNCH_SENT):
        settings.set(FIRST_LAUNCH_SENT, True)
        sender.send("app_first_launch")
        return None
    if background or not late:
        sender.send("app_launch", {"background": background})
        return None

    def held():
        sender.send("app_launch", {"background": bool(wait(LOGIN_SIGNAL_WAIT))})

    thread = threading.Thread(target=held, daemon=True, name="eggie-launch-event")
    thread.start()
    return thread


def app_error(sender: EventSender, code: str, exc: BaseException | None, *,
              home: str, user: str) -> None:
    message = sanitize(f"{type(exc).__name__}: {exc}", home=home, user=user) if exc else ""
    sender.send("app_error", {"error_code": code, "message": message})


def install_hooks(sender: EventSender, *, home: str, user: str) -> None:
    previous_sys = sys.excepthook
    previous_thread = threading.excepthook

    def report(exc: BaseException | None) -> None:
        if not isinstance(exc, Exception):
            return
        try:
            app_error(sender, "uncaught", exc, home=home, user=user)
        except Exception:
            log.debug("could not report an uncaught error", exc_info=True)

    def sys_hook(exc_type, exc, tb):
        report(exc)
        # The interpreter exits next and kills the daemon worker.
        sender.flush(2.0)
        previous_sys(exc_type, exc, tb)

    def thread_hook(args):
        report(args.exc_value)
        previous_thread(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
