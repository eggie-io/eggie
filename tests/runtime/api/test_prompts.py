from __future__ import annotations

from eggie_api.domain.connections import Question
from eggie_api.domain.prompts import Screen, asking, find_code, find_link

apple_id = Question("apple-id", "Apple ID:", "", None, None)
password = Question("password", "Password (for", None, "Apple password", "secret")


def screen(rows, cursor_y, history=0):
    return Screen.from_wire({"rows": list(rows), "cursor_y": cursor_y, "history_size": history,
                             "text": "\n".join(rows)})


def test_the_link_after_its_marker_wins_over_an_earlier_one():
    text = "see https://docs.expo.dev\nOpen https://expo.dev/device?code=AB\n"
    assert find_link(text, "Open ") == "https://expo.dev/device?code=AB"


def test_without_a_marker_the_first_https_link():
    assert find_link("a https://one.dev. b https://two.dev", None) == "https://one.dev"


def test_trailing_punctuation_is_not_part_of_the_link():
    assert find_link("Open https://x.dev/a;", "Open ") == "https://x.dev/a"


def test_no_link_is_none():
    assert find_link("nothing here http://insecure.dev", None) is None
    assert find_link("see https://x.dev", "Open ") is None


def test_the_code_is_the_word_after_its_marker():
    assert find_code("Code: AB12-CD34\n", "Code:") == "AB12-CD34"


def test_no_code_without_a_marker_or_when_it_is_absent():
    assert find_code("Code: AB12", None) is None
    assert find_code("nothing", "Code:") is None


def test_a_question_is_asking_only_on_the_cursor_row():
    rows = ("Apple ID: me@x.com", "Password (for me@x.com): ")
    sources = [("apple", [apple_id, password])]
    assert asking(screen(rows, 1), sources) == ("apple", password)
    assert asking(screen(rows, 0), sources) == ("apple", apple_id)
    assert asking(screen((*rows, ""), 2), sources) is None


def test_a_prompt_wrapped_onto_the_cursor_row_is_asking():
    question = Question("sign-in", "Do you want to log in to your Apple account?", "", None, None)
    wrapped = Screen.from_wire({
        "rows": ["? Do you want to log in to your Apple ac", "count? (Y/n)"], "cursor_y": 1,
        "history_size": 0, "text": "? Do you want to log in to your Apple account? (Y/n)",
        "cursor_line": "? Do you want to log in to your Apple account? (Y/n)"})
    assert asking(wrapped, [("apple", [question])]) == ("apple", question)


def test_a_cursor_outside_the_rows_asks_nothing():
    assert asking(screen(("Apple ID:",), 5), [("apple", [apple_id])]) is None


def test_matching_ignores_case():
    assert asking(screen(("APPLE id: ",), 0), [("apple", [apple_id])]) == ("apple", apple_id)


def test_the_actions_own_question_wins():
    first = Question("go", "continue?", "y", None, None)
    second = Question("go2", "Continue?", "n", None, None)
    sources = [("action", [first]), ("expo", [second])]
    assert asking(screen(("Continue? ",), 0), sources) == ("action", first)


def test_from_wire_reads_the_helper_reply_and_position_adds_history():
    s = Screen.from_wire({"rows": ["a", "b"], "cursor_y": 1, "history_size": 40, "text": "a\nb"})
    assert s == Screen(("a", "b"), 1, 40, "a\nb", "b")
    assert s.position == 41


def test_from_wire_refuses_other_shapes():
    good = {"rows": ["a"], "cursor_y": 0, "history_size": 0, "text": "a"}
    assert Screen.from_wire(good) is not None
    assert Screen.from_wire({k: v for k, v in good.items() if k != "text"}) is None
    assert Screen.from_wire({**good, "rows": "a"}) is None
    assert Screen.from_wire({**good, "rows": [1]}) is None
    assert Screen.from_wire({**good, "cursor_y": -1}) is None
    assert Screen.from_wire({**good, "history_size": -1}) is None
    assert Screen.from_wire({**good, "cursor_y": True}) is None
    assert Screen.from_wire({**good, "cursor_y": "0"}) is None
    assert Screen.from_wire({**good, "cursor_line": None}) is None
    assert Screen.from_wire(None) is None


def test_a_match_ending_in_a_space_finds_the_right_stripped_row():
    # The helper strips each row, so "Apple ID: " is on screen as "Apple ID:".
    spaced = Question("apple-id", "Apple ID: ", None, "Apple ID", "text")
    assert asking(screen(("Apple ID:",), 0), [("apple", [spaced])]) == ("apple", spaced)
