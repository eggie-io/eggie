"""The web page previews a new project's id before the API creates it; both
sides read these cases so the preview can't promise an id the API won't make."""
import json
from pathlib import Path

from eggie_api.domain.project import _slug

CASES = Path(__file__).resolve().parents[2] / "fixtures" / "slugify-cases.json"


def test_the_api_slugs_every_shared_case_the_way_the_page_previews_it():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert cases, "no shared slug cases -- this test is no longer guarding anything"
    for case in cases:
        assert _slug(case["name"]) == case["slug"], case
