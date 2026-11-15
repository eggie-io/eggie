"""Answering a running command's prompts: fixed replies typed by the API, labelled
questions answered by the owner. Answer values are never logged or echoed."""
from __future__ import annotations

import threading
from collections.abc import Sequence

from ..domain.connections import Question, one_line
from ..domain.prompts import Screen, asking
from ..errors import BadRequest, Conflict
from ..infra.actions_helper import HelperError, target_key

MAX_ANSWER = 4096
Sources = Sequence[tuple[str, Sequence[Question]]]


def _not_asking() -> Conflict:
    return Conflict("not_asking", "That question isn't being asked any more.")


def _answer_parts(body: object) -> tuple[str, str, str]:
    if isinstance(body, dict):
        source, question, value = body.get("source"), body.get("question"), body.get("value")
        if (type(source) is str and type(question) is str and type(value) is str
                and len(value) <= MAX_ANSWER and one_line(value) and "\0" not in value):
            return source, question, value
    raise BadRequest("invalid_answer",
                     f"An answer is one line of at most {MAX_ANSWER} characters.")


def _running(run: dict | None) -> bool:
    return isinstance(run, dict) and run.get("state") == "running"


class Prompter:
    def __init__(self, helper):
        self._helper = helper
        self._locks: dict[tuple, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # (target key, run started_at, source, question id) -> (position, text) when answered.
        self._answered: dict[tuple, tuple[int, str]] = {}

    def _lock(self, target: dict) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(target_key(target), threading.Lock())

    def _key(self, target: dict, run: dict, source: str, question: Question) -> tuple:
        return target_key(target), run.get("started_at"), source, question.id

    def _asking(self, target: dict, run: dict, sources: Sources
                ) -> tuple[Screen | None, tuple[str, Question] | None]:
        screen = Screen.from_wire(self._helper.screen(target))
        found = asking(screen, sources) if screen is not None else None
        if found is None:
            return screen, None
        last = self._answered.get(self._key(target, run, *found))
        # A typed echo keeps the position and a resize reflow keeps the joined text;
        # only a real re-ask changes both.
        if last is not None and (last[0] == screen.position or last[1] == screen.text):
            return screen, None
        return screen, found

    def _type(self, target: dict, run: dict, screen: Screen, source: str,
              question: Question, text: str) -> None:
        self._helper.type(target, text)
        mine, started = target_key(target), run.get("started_at")
        for key in [k for k in self._answered if k[0] == mine and k[1] != started]:
            del self._answered[key]
        self._answered[self._key(target, run, source, question)] = (screen.position, screen.text)

    def look(self, target: dict, run: dict | None, sources: Sources
             ) -> tuple[Screen | None, dict | None]:
        """The screen of a running command and the labelled question it is asking.
        A fixed reply is typed here, once."""
        if not _running(run):
            return None, None
        with self._lock(target):
            try:
                screen, found = self._asking(target, run, sources)
                if found is None:
                    return screen, None
                source, question = found
                if question.reply is not None:
                    self._type(target, run, screen, source, question, question.reply)
                    return screen, None
            except HelperError:
                # The run ended between reading its state and its screen.
                return None, None
        return screen, {"source": source, "id": question.id, "label": question.label,
                        "type": question.type}

    def read(self, target: dict, run: dict | None, sources: Sources) -> dict | None:
        return self.look(target, run, sources)[1]

    def answer(self, target: dict, run: dict | None, sources: Sources, body: object) -> None:
        source, question_id, value = _answer_parts(body)
        if not _running(run):
            raise _not_asking()
        with self._lock(target):
            try:
                screen, found = self._asking(target, run, sources)
                if (found is None or found[0] != source or found[1].id != question_id
                        or found[1].label is None):
                    raise _not_asking()
                self._type(target, run, screen, source, found[1], value)
            except HelperError as e:
                if e.code == "not_running":
                    raise _not_asking()
                raise
