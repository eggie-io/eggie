"""Which screen Home draws, and the one thing it does first.

Carried out of app.js's refresh(): the order of these rules is the contract.
A resumed launch that showed first-run would restart setup from scratch; a
runtime update retried on every refresh would loop forever on an update that
"succeeds" without fixing the API.
"""
from __future__ import annotations

from host.desktop.view import Screen, screen_for


def _screen(route="home", state="running", **flags):
    defaults = dict(first_run=False, resumed=False, enter_console=False,
                    update_tried=False, update_error="", problem="")
    return screen_for(route, state, **{**defaults, **flags})


def test_a_resumed_launch_continues_setup_even_on_a_virgin_machine():
    # RunOnce relaunched after the reboot gate; vm_exists is still False and
    # nothing is recorded, which is exactly the first-run shape.
    assert _screen("home", "not_installed", resumed=True, first_run=True).action == "start_install"


def test_a_virgin_machine_sees_the_welcome_screen():
    assert _screen("home", "not_installed", first_run=True) == Screen("first-run")


def test_the_console_entry_falls_back_to_the_running_home():
    # The page enters the console itself; if the handoff code cannot be had,
    # the screen it is told to draw must still be true for the machine.
    assert _screen(enter_console=True) == Screen("home:running", action="enter_console")


def test_an_old_runtime_api_starts_the_update_once():
    first = _screen("update_runtime", "")
    assert (first.name, first.action) == ("runtime-update:running", "start_runtime_update")
    again = _screen("update_runtime", "", update_tried=True, update_error="ghcr timed out")
    assert again == Screen("runtime-update:failed", message="ghcr timed out")


def test_a_failed_update_with_no_job_error_shows_the_probe_problem():
    # problem is empty when the runtime answers but speaks an old API; when
    # it is not, it is the better sentence.
    assert _screen("update_runtime", "", update_tried=True,
                   problem="api 3 not supported").message == "api 3 not supported"


def test_every_screen_for_result_is_a_name_the_template_check_covers():
    """SCREENS is what test_ui_assets checks for templates; a name screen_for
    can return but SCREENS omits is a blank window nothing notices."""
    from itertools import product

    from host.desktop.view import HOME_STATES, SCREENS

    routes = [("home", state) for state in HOME_STATES] + \
        [("unreachable", ""), ("unresponsive", ""), ("update_runtime", "")]
    for (route, state), flag in product(routes, ("", "first_run", "resumed",
                                                  "enter_console", "update_tried")):
        name = _screen(route, state, **({flag: True} if flag else {})).name
        assert name in SCREENS, f"{route}/{state} with {flag or 'no flag'} -> {name}"
