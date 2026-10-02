from __future__ import annotations

import pytest

from host.desktop.lifecycle import TRAY_ONLY, WINDOW, launch_mode, turn_on_autostart_once
from host.desktop.settings import AUTOSTART_SET_ONCE, Settings


def _exists(value):
    def check():
        if isinstance(value, Exception):
            raise value
        return value
    return check


@pytest.mark.parametrize("resume, background, exists, expected", [
    (True, True, True, WINDOW),       # --resume wins: setup must continue on screen
    (True, False, True, WINDOW),
    (False, True, False, WINDOW),     # login launch before setup ever finished
    (False, True, True, TRAY_ONLY),
    (False, False, True, WINDOW),
    (False, False, False, WINDOW),
])
def test_launch_mode(resume, background, exists, expected):
    assert launch_mode(resume=resume, background=background,
                       vm_exists=_exists(exists)) == expected


def test_a_provider_that_cannot_answer_opens_the_window():
    # A hidden window over a broken provider is an app the user cannot find.
    assert launch_mode(resume=False, background=True,
                       vm_exists=_exists(RuntimeError("wsl.exe missing"))) == WINDOW


def test_a_normal_launch_never_asks_the_provider():
    def boom():
        raise AssertionError("exists() probed on a plain launch")
    assert launch_mode(resume=False, background=False, vm_exists=boom) == WINDOW


def test_the_first_successful_install_turns_autostart_on(tmp_path):
    settings, calls = Settings(tmp_path / "s.json"), []
    turn_on_autostart_once(settings, available=True, enable=lambda: calls.append(True))
    assert calls == [True]
    assert settings.get(AUTOSTART_SET_ONCE) is True


def test_autostart_is_turned_on_only_once(tmp_path):
    settings, calls = Settings(tmp_path / "s.json"), []
    turn_on_autostart_once(settings, available=True, enable=lambda: calls.append(1))
    turn_on_autostart_once(settings, available=True, enable=lambda: calls.append(2))
    assert calls == [1]


def test_a_failing_enable_still_sets_the_flag(tmp_path):
    settings = Settings(tmp_path / "s.json")

    def fail():
        raise OSError("registry locked")

    turn_on_autostart_once(settings, available=True, enable=fail)
    assert settings.get(AUTOSTART_SET_ONCE) is True


def test_an_unavailable_autostart_is_left_for_a_later_frozen_run(tmp_path):
    settings, calls = Settings(tmp_path / "s.json"), []
    turn_on_autostart_once(settings, available=False, enable=lambda: calls.append(1))
    assert calls == []
    assert settings.get(AUTOSTART_SET_ONCE) is False
