"""Which screen the launch draws, and the one thing it does first.

The order of these rules is the contract: a resumed launch that showed the
account screen would restart setup from scratch; a runtime update retried on
every refresh would loop forever on an update that "succeeds" without fixing
the API.
"""
from __future__ import annotations

from itertools import product

from host.desktop.view import HOME_STATES, SCREENS, Screen, newest_owned, screen_for

KITCHEN = {"id": "k1", "is_owner": True, "created_at": "2026-01-01T00:00:00Z"}


def _screen(route="home", state="running", **flags):
    defaults = dict(signed_in=True, pending_sign_in=False, kitchens=[KITCHEN],
                    kitchens_error="", vm_exists=False, resumed=False,
                    just_signed_in=False, update_tried=False, update_error="",
                    problem="")
    return screen_for(route=route, state=state, **{**defaults, **flags})


def test_not_signed_in_wins_over_everything_but_a_resumed_install():
    assert _screen(signed_in=False, resumed=True).action == "start_install"
    assert _screen("update_runtime", "", signed_in=False) == Screen("account")
    assert _screen(signed_in=False, pending_sign_in=True) == Screen("account:waiting")


def test_kitchens_not_loaded_yet_asks_the_page_to_load_them():
    assert _screen(kitchens=None) == Screen("home", action="load_kitchens")


def test_account_without_kitchens_or_vm_chooses_where():
    assert _screen(kitchens=[], vm_exists=False) == Screen("where")


def test_a_local_vm_or_a_service_error_keeps_the_user_on_home():
    assert _screen(kitchens=[], vm_exists=True) == Screen("home")
    assert _screen(kitchens=None, kitchens_error="down") == Screen("home")


def test_first_sign_in_with_kitchens_opens_connect_on_newest_owned():
    older = {**KITCHEN, "id": "old", "created_at": "2025-01-01T00:00:00Z"}
    newer = {**KITCHEN, "id": "new", "created_at": "2026-06-01T00:00:00Z"}
    assert _screen(kitchens=[older, newer], just_signed_in=True) == Screen("connect", message="new")


def test_org_kitchen_not_owned_is_never_chosen_and_cannot_connect():
    theirs = {**KITCHEN, "id": "theirs", "is_owner": False, "created_at": "2030-01-01T00:00:00Z"}
    assert newest_owned([theirs, KITCHEN])["id"] == "k1"
    assert newest_owned([theirs]) is None
    assert _screen(kitchens=[theirs], just_signed_in=True, vm_exists=True) == Screen("home")


def test_service_down_still_shows_home_with_local_card():
    screen = _screen("home", "running", kitchens=None, kitchens_error="down", vm_exists=True)
    assert screen == Screen("home")


def test_an_old_runtime_api_starts_the_update_once():
    first = _screen("update_runtime", "", vm_exists=True)
    assert (first.name, first.action) == ("runtime-update:running", "start_runtime_update")
    again = _screen("update_runtime", "", vm_exists=True, update_tried=True,
                    update_error="ghcr timed out")
    assert again == Screen("runtime-update:failed", message="ghcr timed out")


def test_a_failed_update_with_no_job_error_shows_the_probe_problem():
    assert _screen("update_runtime", "", vm_exists=True, update_tried=True,
                   problem="api 3 not supported").message == "api 3 not supported"


def test_broken_machines_keep_their_own_screens():
    assert _screen("unreachable", "", vm_exists=True).name == "unreachable"
    assert _screen("unresponsive", "", vm_exists=True).name == "unresponsive"


def test_every_screen_for_result_is_a_name_the_template_check_covers():
    """SCREENS is what test_ui_assets checks for templates; a name screen_for
    can return but SCREENS omits is a blank window nothing notices."""
    routes = [("home", state) for state in HOME_STATES] + \
        [("unreachable", ""), ("unresponsive", ""), ("update_runtime", "")]
    flags = ("", "signed_out", "pending", "loading", "empty", "resumed",
             "just_signed_in", "update_tried")
    overrides = {
        "signed_out": dict(signed_in=False), "pending": dict(signed_in=False, pending_sign_in=True),
        "loading": dict(kitchens=None), "empty": dict(kitchens=[]),
        "resumed": dict(resumed=True), "just_signed_in": dict(just_signed_in=True),
        "update_tried": dict(update_tried=True), "": {},
    }
    for (route, state), flag, vm in product(routes, flags, (True, False)):
        name = _screen(route, state, vm_exists=vm, **overrides[flag]).name
        assert name in SCREENS, f"{route}/{state}/{flag}/vm={vm} -> {name}"
