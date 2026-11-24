"""Making a cloud kitchen (1d) and watching it get ready (1e)."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from host.desktop.view import (checkout_url, checkout_wait, default_kitchen_name, from_price,
                               new_kitchen, region_tiles, setup_rows, size_tiles)

INSTANCES = {
    "regions": [
        {"id": "north-america", "label": "North America", "enabled": True,
         "size_ids_with_room": ["medium"]},
        {"id": "europe", "label": "Europe", "enabled": True,
         "size_ids_with_room": ["small", "medium"]},
        {"id": "asia", "label": "Asia", "enabled": False, "size_ids_with_room": []},
    ],
    "sizes": [
        {"id": "small", "label": "S", "description": "sites and small apps",
         "vcpus": 1, "memory_gb": 2, "disk_gb": 40, "enabled": True},
        {"id": "medium", "label": "M", "description": "apps with a database",
         "vcpus": 2, "memory_gb": 4, "disk_gb": 80, "enabled": False},
        {"id": "large", "label": "L", "description": None,
         "vcpus": 4, "memory_gb": 8, "disk_gb": 160, "enabled": True},
    ],
}


def _billing(**sizes):
    return {"enabled": True, "trial_days": None, "sizes": [
        {"size_id": size_id, "monthly": monthly, "yearly": None, "covered_by_license": False}
        for size_id, monthly in sizes.items()]}


def _by_id(tiles):
    return {tile["id"]: tile for tile in tiles}


def test_sizes_follow_the_service_flags():
    billing = _billing(small={"amount": 2400, "currency": "usd"},
                       medium={"amount": 4800, "currency": "usd"}, large=None)
    tiles = _by_id(size_tiles(INSTANCES, billing))
    assert (tiles["medium"]["note"], tiles["medium"]["enabled"]) == ("Coming soon", False)
    assert (tiles["large"]["note"], tiles["large"]["enabled"]) == ("No price yet", False)
    assert (tiles["small"]["note"], tiles["small"]["enabled"]) == ("", True)
    assert tiles["small"]["line"] == "1 vCPU · 2 GB · sites and small apps"


def test_a_licensed_size_is_pickable_without_a_price():
    billing = _billing(small=None)
    billing["sizes"][0]["covered_by_license"] = True
    tile = _by_id(size_tiles(INSTANCES, billing))["small"]
    assert (tile["note"], tile["price"], tile["enabled"]) == ("Covered by your license", "", True)


def test_price_in_cents_reads_as_dollars():
    billing = _billing(small={"amount": 2400, "currency": "usd"},
                       large={"amount": 1250, "currency": "eur"})
    tiles = _by_id(size_tiles(INSTANCES, billing))
    assert tiles["small"]["price"] == "$24/mo"
    assert tiles["large"]["price"] == "EUR 12.50/mo"


def test_from_price_is_the_cheapest_size_that_can_be_picked():
    billing = _billing(small={"amount": 2400, "currency": "usd"},
                       medium={"amount": 900, "currency": "usd"},
                       large={"amount": 4800, "currency": "usd"})
    assert from_price(size_tiles(INSTANCES, billing)) == "$24"
    assert from_price(size_tiles(INSTANCES, _billing(small=None, large=None))) == ""


def test_full_region_says_no_room():
    regions = _by_id(region_tiles(INSTANCES, "small"))
    assert (regions["north-america"]["note"], regions["north-america"]["enabled"]) == (
        "No room right now", False)
    assert (regions["europe"]["note"], regions["europe"]["enabled"]) == ("", True)
    assert (regions["asia"]["note"], regions["asia"]["enabled"]) == ("Coming soon", False)


def test_checkout_url_encodes_the_name():
    url = checkout_url("https://app.eggie.io", "Ihor’s cloud & co", "small", "europe")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://app.eggie.io/kitchens/new/checkout")
    assert parse_qs(parts.query) == {"name": ["Ihor’s cloud & co"], "size": ["small"],
                                     "region": ["europe"], "source": ["desktop"]}


def test_default_name_uses_the_first_name_when_there_is_one():
    assert default_kitchen_name({"user": {"first_name": " Ihor "}}) == "Ihor’s cloud"
    assert default_kitchen_name({"user": {"first_name": ""}}) == "My cloud"
    assert default_kitchen_name(None) == "My cloud"


def test_new_kitchen_ignores_ids_seen_before_and_others_kitchens():
    rows = [
        {"id": "old", "is_owner": True, "created_at": "2026-11-24T10:00:00Z"},
        {"id": "teammate", "is_owner": False, "created_at": "2026-11-24T12:00:00Z"},
        {"id": "mine", "is_owner": True, "created_at": "2026-11-24T11:00:00Z"},
    ]
    assert new_kitchen(frozenset({"old"}), rows)["id"] == "mine"
    assert new_kitchen(frozenset({"old", "mine"}), rows) is None


@pytest.mark.parametrize("elapsed,wait", [(0, 3.0), (119.9, 3.0), (120, 10.0), (86400 * 7, 10.0)])
def test_cancelled_payment_keeps_waiting_until_back(elapsed, wait):
    # A cancelled payment looks like one still open; only Back ends the wait.
    assert checkout_wait(elapsed) == wait


def test_a_failed_setup_marks_the_row_it_stopped_on():
    assert setup_rows("failed", "dns") == ["done", "done", "done", "failed", "waiting"]
    assert setup_rows("provisioning", "server") == [
        "done", "running", "waiting", "waiting", "waiting"]
    assert setup_rows("running", None) == ["done"] * 5
