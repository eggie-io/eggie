from __future__ import annotations

import pytest

from eggie_api.domain.connections import Question, parse_connections

EXPO = {
    "id": "expo", "name": "Expo", "description": "Your Expo account.",
    "login": {"command": "npx --yes eas-cli@latest login --device", "link_after": "Open ",
              "code_after": "Code: "},
    "check": "npx --yes eas-cli@latest whoami",
    "questions": [{"id": "continue", "match": "Do you want to continue?", "reply": "",
                   "label": None, "type": None}],
}
APPLE = {
    "id": "apple", "name": "Apple", "description": None, "login": None, "check": None,
    "questions": [
        {"id": "sign-in", "match": "Do you want to log in to your Apple account?", "reply": "",
         "label": None, "type": None},
        {"id": "apple-id", "match": "Apple ID:", "reply": None, "label": "Apple ID (email)", "type": "text"},
        {"id": "password", "match": "Password (for ", "reply": None, "label": "Apple ID password",
         "type": "secret"},
    ],
}
ASK = {"id": "q", "match": "Go?", "reply": ""}


def parse(*entries):
    return parse_connections({"connections": list(entries)})


def test_the_contracts_expo_and_apple_entries_parse():
    found, rejected = parse(EXPO, APPLE)
    assert rejected == set() and list(found) == ["expo", "apple"]
    expo, apple = found["expo"], found["apple"]
    assert expo.login.link_after == "Open " and expo.login.code_after == "Code: "
    assert expo.check == "npx --yes eas-cli@latest whoami"
    assert apple.login is None and apple.check is None
    assert apple.questions[2] == Question("password", "Password (for ", None, "Apple ID password", "secret")
    assert expo.to_wire() == {"id": "expo", "name": "Expo", "description": "Your Expo account.",
                              "login": True, "check": True}
    assert apple.to_wire()["login"] is False and apple.to_wire()["check"] is False


@pytest.mark.parametrize("question", [
    {**ASK, "label": "Both"}, {"id": "q", "match": "Go?"},
    {**ASK, "type": "text"},
])
def test_a_question_without_exactly_one_of_reply_and_label_is_refused(question):
    found, rejected = parse({**APPLE, "id": "bad", "questions": [question]}, EXPO)
    assert list(found) == ["expo"] and rejected == {"bad"}


def test_an_empty_reply_is_a_reply():
    found, _ = parse({**APPLE, "questions": [{**ASK, "reply": ""}]})
    assert found["apple"].questions[0] == Question("q", "Go?", "", None, None)


def test_a_label_question_without_type_is_text():
    found, _ = parse({**APPLE, "questions": [{"id": "q", "match": "Go?", "label": "Name"}]})
    assert found["apple"].questions[0].type == "text"


def test_a_connection_with_neither_login_nor_questions_is_dropped():
    found, rejected = parse({"id": "empty", "name": "Empty"}, EXPO)
    assert list(found) == ["expo"] and rejected == {"empty"}


def test_duplicate_question_ids_in_one_connection_are_refused():
    found, rejected = parse({**APPLE, "questions": [ASK, ASK]})
    assert found == {} and rejected == {"apple"}


def test_a_repeated_connection_id_keeps_the_first():
    found, rejected = parse(EXPO, {**EXPO, "name": "Second"})
    assert found["expo"].name == "Expo" and rejected == set()


@pytest.mark.parametrize("bad", [
    {"name": "x" * 81}, {"questions": [{**ASK, "match": "a\nb"}]},
    {"questions": [{**ASK, "match": "x" * 201}]},
    {"login": {"command": "c", "link_after": ""}}, {"login": {"command": "c", "link_after": "x" * 41}},
    {"login": {"command": "a\0b"}}, {"questions": [{**ASK, "id": f"q{i}"} for i in range(21)]},
    {"questions": [{"id": "q", "match": "Go?", "label": "L", "type": "password"}]},
])
def test_a_bad_field_drops_the_connection(bad):
    found, rejected = parse({**EXPO, **bad}, {**APPLE})
    assert list(found) == ["apple"] and rejected == {"expo"}


def test_an_entry_without_a_valid_id_is_dropped_but_names_nothing():
    found, rejected = parse({**EXPO, "id": "Bad_Id"}, {**APPLE})
    assert list(found) == ["apple"] and rejected == set()


@pytest.mark.parametrize("payload", [None, [], {"connections": "x"}, {}])
def test_a_malformed_body_yields_no_connections(payload):
    assert parse_connections(payload) == ({}, set())
