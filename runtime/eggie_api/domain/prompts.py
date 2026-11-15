"""Reading a tmux screen of a running sign-in: its link, code and the question on the cursor row."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .connections import Question

_LINK = re.compile(r"https://\S+")
_WORD = re.compile(r"\S+")


def _is_count(value: object) -> bool:
    return type(value) is int and value >= 0


@dataclass(frozen=True)
class Screen:
    rows: tuple[str, ...]
    cursor_y: int
    history_size: int
    text: str

    @property
    def position(self) -> int:
        return self.history_size + self.cursor_y

    @staticmethod
    def from_wire(d: object) -> Screen | None:
        if not isinstance(d, dict):
            return None
        rows, cursor_y = d.get("rows"), d.get("cursor_y")
        history_size, text = d.get("history_size"), d.get("text")
        if not isinstance(rows, list) or not all(type(r) is str for r in rows):
            return None
        if not _is_count(cursor_y) or not _is_count(history_size) or type(text) is not str:
            return None
        return Screen(tuple(rows), cursor_y, history_size, text)


def _tail(text: str, after: str | None) -> str | None:
    if after is None:
        return text
    at = text.find(after)
    return None if at < 0 else text[at + len(after):]


def find_link(text: str, after: str | None) -> str | None:
    tail = _tail(text, after)
    found = _LINK.search(tail) if tail is not None else None
    return found.group().rstrip(".,;") if found else None


def find_code(text: str, after: str | None) -> str | None:
    tail = _tail(text, after) if after is not None else None
    found = _WORD.search(tail) if tail is not None else None
    return found.group() if found else None


def asking(screen: Screen,
           sources: Sequence[tuple[str, Sequence[Question]]]) -> tuple[str, Question] | None:
    """The first question whose match is on the cursor's row, in source then file order."""
    if screen.cursor_y >= len(screen.rows):
        return None
    row = screen.rows[screen.cursor_y].lower()
    for source, questions in sources:
        for question in questions:
            if question.match.lower() in row:
                return source, question
    return None
