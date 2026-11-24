"""What Home's cards say: mapping kitchen rows and the local VM to cards."""
from __future__ import annotations

import pytest

from host.desktop.view import Screen, cloud_card, home_view, local_card, setup_progress


def _row(**fields):
    return {"id": "k1", "name": "slug", "display_name": "", "status": "running",
            "step": None, "is_owner": True, "console_hostname": "a.eggie.app",
            "size_label": "Small", "region_label": "Europe", **fields}


@pytest.mark.parametrize("status,step,done", [
    ("provisioning", "certificate", 0),
    ("provisioning", "server", 1),
    ("provisioning", "address", 2),
    ("failed", "dns", 3),
    ("booting", None, 4),
    ("starting", None, 4),
    ("running", None, 5),
])
def test_setup_progress_counts_the_five_rows(status, step, done):
    assert setup_progress(status, step) == (done, 5)


def test_provisioning_kitchen_card_shows_steps_done():
    card = cloud_card(_row(status="provisioning", step="address", display_name="Bakery"))
    assert (card["title"], card["state"], card["steps_done"]) == ("Bakery", "getting_ready", 2)
    assert card["badge"] == "" and card["can_connect"] is False


def test_running_card_has_badge_line_and_connect_only_for_the_owner():
    card = cloud_card(_row())
    assert card["title"] == "slug"
    assert card["badge"] == "Running"
    assert card["line"] == "a.eggie.app · Small · Europe"
    assert card["can_connect"] is True
    assert cloud_card(_row(is_owner=False))["can_connect"] is False


def test_card_line_skips_missing_parts():
    assert cloud_card(_row(size_label=None, region_label=""))["line"] == "a.eggie.app"


@pytest.mark.parametrize("route,state,expected", [
    ("home", "running", "running"), ("home", "stopped", "stopped"),
    ("home", "wrong", "failed"), ("home", "not_installed", "none"),
    ("unreachable", "", "failed"), ("unresponsive", "", "failed"),
])
def test_local_card_state(route, state, expected):
    assert local_card(route, state) == expected


def test_service_down_still_shows_home_with_local_card():
    view = home_view(None, "down", "running", vm_exists=True)
    assert [c["kind"] for c in view["cards"]] == ["local"]
    assert view["cloud_error"] == "down"
    assert view["missing"] == ""


def test_missing_kind_gets_the_dashed_card():
    rows = [_row()]
    assert home_view(rows, "", "none", False)["missing"] == "local"
    assert home_view([], "", "stopped", True)["missing"] == "cloud"
    both = home_view(rows, "", "running", True)
    assert both["missing"] == ""
    assert [c["kind"] for c in both["cards"]] == ["cloud", "local"]


def test_doctor_only_with_a_local_vm():
    assert home_view([_row()], "", "none", False)["show_doctor"] is False
    assert home_view([_row()], "", "failed", True)["show_doctor"] is True


def test_unreachable_local_vm_is_a_failed_card_next_to_a_running_cloud_one():
    view = home_view([_row()], "", local_card("unreachable", ""), True,
                     repair=Screen("unreachable", message="refused"))
    cloud, local = view["cards"]
    assert (cloud["state"], local["state"]) == ("running", "failed")
    assert (local["repair"], local["repair_message"]) == ("unreachable", "refused")


@pytest.mark.parametrize("status,seconds", [
    ("running", 30), ("provisioning", 3), ("starting", 3), ("stopping", 3), ("stopped", 30),
])
def test_home_polls_fast_only_while_a_kitchen_is_changing(status, seconds):
    assert home_view([_row(status=status)], "", "none", False)["poll_seconds"] == seconds
