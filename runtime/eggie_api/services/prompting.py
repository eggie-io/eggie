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


def parse_answer(body: object) -> tuple[str, str, str]:
    """`(source, question, value)` from an answer body, or `400 invalid_answer` without the value."""
    if isinstance(body, dict):
        source, question, value = body.get("source"), body.get("question"), body.get("value")
        if (type(source) is str and type(question) is str and type(value) is str
                and len(value) <= MAX_ANSWER and one_line(value) and "\0" not in value):
            return source, question, value
    raise BadRequest("invalid_answer",
                     f"An answer is one line of at most {MAX_ANSWER} characters.")


def _running(run: dict | None) -> bool:
    return isinstance(run, dict) and run.get("state") == "running"


class _Target:
    """One target's lock and what was answered in it, touched only under that lock:
    (run started_at, source, question id) -> (position, text) last seen answered."""

    def __init__(self):
        self.lock = threading.Lock()
        self.answered: dict[tuple, tuple[int, str]] = {}


class Prompter:
    def __init__(self, helper):
        self._helper = helper
        self._targets: dict[tuple, _Target] = {}
        self._guard = threading.Lock()

    def _target(self, target: dict) -> _Target:
        with self._guard:
            return self._targets.setdefault(target_key(target), _Target())

    def _asking(self, mine: _Target, target: dict, run: dict, sources: Sources
                ) -> tuple[Screen | None, tuple[str, Question] | None]:
        screen = Screen.from_wire(self._helper.screen(target))
        found = asking(screen, sources) if screen is not None else None
        if found is None:
            return screen, None
        key = (run.get("started_at"), found[0], found[1].id)
        last = mine.answered.get(key)
        # A typed echo keeps the position and a resize reflow keeps the joined text;
        # only a real re-ask changes both. Each suppressed sighting moves the mark, so an
        # echo followed by a reflow is still the answered prompt.
        if last is not None and (last[0] == screen.position or last[1] == screen.text):
            mine.answered[key] = (screen.position, screen.text)
            return screen, None
        return screen, found

    def _type(self, mine: _Target, target: dict, run: dict, screen: Screen, source: str,
              question: Question, text: str) -> None:
        self._helper.type(target, text)
        started = run.get("started_at")
        for key in [k for k in mine.answered if k[0] != started]:
            del mine.answered[key]
        mine.answered[(started, source, question.id)] = (screen.position, screen.text)

    def forget(self, target: dict) -> None:
        """Drop what was answered: a run restarted within a second keeps its started_at."""
        mine = self._target(target)
        with mine.lock:
            mine.answered.clear()

    def look(self, target: dict, run: dict | None, sources: Sources
             ) -> tuple[Screen | None, dict | None]:
        """The screen of a running command and the labelled question it is asking.
        A fixed reply is typed here, once."""
        if not _running(run):
            return None, None
        mine = self._target(target)
        with mine.lock:
            try:
                screen, found = self._asking(mine, target, run, sources)
                if found is None:
                    return screen, None
                source, question = found
                if question.reply is not None:
                    self._type(mine, target, run, screen, source, question, question.reply)
                    return screen, None
            except HelperError as e:
                # The run ended between reading its state and its screen.
                if e.code == "not_running":
                    return None, None
                raise
        return screen, {"source": source, "id": question.id, "label": question.label,
                        "type": question.type}

    def read(self, target: dict, run: dict | None, sources: Sources) -> dict | None:
        return self.look(target, run, sources)[1]

    def answer(self, target: dict, run: dict | None, sources: Sources, body: object) -> None:
        source, question_id, value = parse_answer(body)
        if not _running(run):
            raise _not_asking()
        mine = self._target(target)
        with mine.lock:
            try:
                screen, found = self._asking(mine, target, run, sources)
                if (found is None or found[0] != source or found[1].id != question_id
                        or found[1].label is None):
                    raise _not_asking()
                self._type(mine, target, run, screen, source, found[1], value)
            except HelperError as e:
                if e.code == "not_running":
                    raise _not_asking()
                raise
