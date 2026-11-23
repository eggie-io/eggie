"""When the desktop app says it launched, and what an uncaught error sends."""
from __future__ import annotations

import sys
import threading

from host.core.events import EventSender
from host.desktop.settings import FIRST_LAUNCH_SENT, Settings
from host.desktop.telemetry import install_hooks, report_launch


def _never(timeout):
    raise AssertionError("must not wait")


def _launch(events, settings, **kwargs):
    thread = report_launch(events.sender, settings, **kwargs)
    if thread is not None:
        thread.join(5)


def test_first_launch_is_sent_once_then_launches_follow(tmp_path, events):
    settings = Settings(tmp_path / "settings.json")
    _launch(events, settings, background=False, late=False, wait=_never)
    _launch(events, settings, background=False, late=False, wait=_never)
    assert events.sent() == [("app_first_launch", {}), ("app_launch", {"background": False})]
    assert settings.get(FIRST_LAUNCH_SENT)


def test_a_first_launch_is_never_held_for_the_login_signal(tmp_path, events):
    waited = []
    _launch(events, Settings(tmp_path / "settings.json"), background=False, late=True,
            wait=lambda timeout: waited.append(timeout) or True)
    assert events.sent() == [("app_first_launch", {})]
    assert waited == []


def _seen(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    settings.set(FIRST_LAUNCH_SENT, True)
    return settings


def test_mac_waits_for_the_login_signal(tmp_path, events):
    waited = []
    _launch(events, _seen(tmp_path), background=False, late=True,
            wait=lambda timeout: waited.append(timeout) or True)
    assert events.sent() == [("app_launch", {"background": True})]
    assert waited == [10.0]


def test_mac_without_the_signal_is_a_normal_launch(tmp_path, events):
    _launch(events, _seen(tmp_path), background=False, late=True, wait=lambda timeout: False)
    assert events.sent() == [("app_launch", {"background": False})]


def test_a_known_background_launch_is_not_held(tmp_path, events):
    _launch(events, _seen(tmp_path), background=True, late=True, wait=_never)
    assert events.sent() == [("app_launch", {"background": True})]


def test_a_run_that_sends_nothing_leaves_the_first_launch_owed(tmp_path):
    # A source run on the same machine must not use up the installed app's first launch.
    settings = Settings(tmp_path / "settings.json")
    report_launch(EventSender(None, {}), settings, background=False, late=False, wait=_never)
    assert not settings.get(FIRST_LAUNCH_SENT)


def test_an_uncaught_thread_error_is_sent_and_still_reaches_the_old_hook(monkeypatch, events):
    seen = []
    monkeypatch.setattr(threading, "excepthook", lambda args: seen.append(args.exc_value))
    monkeypatch.setattr(sys, "excepthook", lambda *a: None)
    install_hooks(events.sender, home="/home/ana", user="ana")

    def boom():
        raise OSError("cannot read /home/ana/.eggie/x")

    thread = threading.Thread(target=boom)
    thread.start()
    thread.join(5)

    assert events.sent() == [("app_error", {"error_code": "uncaught",
                                            "message": "OSError: cannot read ~/.eggie/x"})]
    assert isinstance(seen[0], OSError)
