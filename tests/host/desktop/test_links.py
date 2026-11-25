"""eggie:// links: any web page can open one, so only a well-formed kitchen id passes."""
from __future__ import annotations

import pytest

from host.desktop.links import kitchen_from_link


def test_a_connect_link_names_its_kitchen():
    assert kitchen_from_link("eggie://connect/k1") == "k1"
    assert kitchen_from_link("EGGIE://Connect/3f2a-b_9/") == "3f2a-b_9"


@pytest.mark.parametrize("url", [
    "", "eggie://connect", "eggie://connect/", "https://connect/k1", "eggie://open/k1",
    "eggie://connect/k1/more", "eggie://connect/..%2Fk1",
    "eggie://connect/-k1", "eggie://connect/" + "a" * 65,
])
def test_anything_else_names_no_kitchen(url):
    assert kitchen_from_link(url) is None
