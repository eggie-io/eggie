from __future__ import annotations

import pytest

from eggie_api.domain.actions import merge, parse_actions, recipes_of

IOS = {
    "id": "ios-build",
    "name": "Sign in to Apple and build",
    "description": "Builds the iPhone app on Expo's servers. Asks for your Expo and Apple sign-in.",
    "command": "npx --yes eas-cli@latest build --platform ios",
    "type": "one-off",
}


def test_a_valid_action_is_kept_with_its_recipe():
    actions, problems = parse_actions("mobile-app", {"slug": "mobile-app", "version": 3, "actions": [IOS]})
    assert problems == [] and actions[0].recipe == "mobile-app" and actions[0].type == "one-off"
    assert actions[0].to_wire() == {**IOS, "recipe": "mobile-app"}


def test_description_may_be_absent_or_null_and_unknown_keys_are_ignored():
    bare = {k: v for k, v in IOS.items() if k != "description"}
    actions, problems = parse_actions("r", {"actions": [bare, {**IOS, "id": "b", "description": None, "extra": 1}]})
    assert problems == [] and [a.description for a in actions] == [None, None]


@pytest.mark.parametrize("bad", [
    {**IOS, "id": "Ios"}, {**IOS, "id": "a" * 64}, {**IOS, "name": "two\nlines"}, {**IOS, "name": "x" * 81},
    {**IOS, "description": "x" * 301}, {**IOS, "description": "a\nb"}, {**IOS, "command": ""},
    {**IOS, "command": "a\0b"}, {**IOS, "command": "x" * 4001}, {**IOS, "type": "daemon"},
    {**IOS, "id": "ios-build\n"}, {**IOS, "name": True}, "not a dict",
    {k: v for k, v in IOS.items() if k != "command"},
])
def test_a_broken_action_is_dropped_with_a_problem(bad):
    actions, problems = parse_actions("mobile-app", {"actions": [bad, {**IOS, "id": "ok"}]})
    assert [a.id for a in actions] == ["ok"] and len(problems) == 1


@pytest.mark.parametrize("payload", [None, [], {"actions": "x"}, {}])
def test_a_malformed_body_yields_no_actions(payload):
    actions, problems = parse_actions("mobile-app", payload)
    assert actions == [] and len(problems) == 1


def test_only_the_first_twenty_are_kept():
    many = [{**IOS, "id": f"a{i}"} for i in range(25)]
    actions, problems = parse_actions("r", {"actions": many})
    assert [a.id for a in actions] == [f"a{i}" for i in range(20)] and len(problems) == 1


def test_an_id_in_two_recipes_runs_from_neither():
    one, _ = parse_actions("one", {"actions": [IOS, {**IOS, "id": "solo"}]})
    two, _ = parse_actions("two", {"actions": [IOS]})
    by_id, problems = merge({"one": one, "two": two})
    assert list(by_id) == ["solo"]
    assert len(problems) == 1 and problems[0]["code"] == "duplicate_action" and problems[0]["recipe"] is None


def test_recipes_of_keeps_valid_unique_slugs_in_order():
    assert recipes_of({"recipes": ["mobile-app", "Bad Slug", "mobile-app", 3, "stripe"]}) == ("mobile-app", "stripe")


@pytest.mark.parametrize("project", [{"recipes": "mobile-app"}, {}, {"recipes": None}])
def test_recipes_of_ignores_a_non_list(project):
    assert recipes_of(project) == ()
