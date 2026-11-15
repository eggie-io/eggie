"""Connections: the service's catalog of sign-ins and prompts a command may hit. No I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass

SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
MAX_SLUG = 63
MAX_NAME = 80
MAX_DESCRIPTION = 300
MAX_COMMAND = 4000
MAX_MARKER = 40
MAX_QUESTIONS = 20
MAX_MATCH = 200
MAX_REPLY = 200
MAX_LABEL = 80
QUESTION_TYPES = ("text", "secret")


@dataclass(frozen=True)
class Question:
    id: str
    match: str
    reply: str | None
    label: str | None
    type: str | None


@dataclass(frozen=True)
class Login:
    command: str
    link_after: str | None
    code_after: str | None


@dataclass(frozen=True)
class Connection:
    id: str
    name: str
    description: str | None
    login: Login | None
    check: str | None
    questions: tuple[Question, ...]

    def to_wire(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description,
                "login": self.login is not None, "check": self.check is not None}


def is_slug(value: object) -> bool:
    return type(value) is str and len(value) <= MAX_SLUG and SLUG.fullmatch(value) is not None


def one_line(value: str) -> bool:
    return "\n" not in value and "\r" not in value


def _text(value: object, low: int, high: int) -> bool:
    return type(value) is str and low <= len(value) <= high and one_line(value)


def is_command(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= MAX_COMMAND and "\0" not in value


def _question(raw: object) -> Question | str:
    if not isinstance(raw, dict):
        return "a question is not an object"
    qid, match = raw.get("id"), raw.get("match")
    reply, label, kind = raw.get("reply"), raw.get("label"), raw.get("type")
    if not is_slug(qid):
        return "a question has an invalid id"
    if not _text(match, 1, MAX_MATCH):
        return f"question {qid} has an invalid match"
    if (reply is None) == (label is None):
        return f"question {qid} needs exactly one of reply and label"
    if reply is not None:
        if not _text(reply, 0, MAX_REPLY):
            return f"question {qid} has an invalid reply"
        if kind is not None:
            return f"question {qid} has a type but no label"
        return Question(qid, match, reply, None, None)
    if not _text(label, 1, MAX_LABEL):
        return f"question {qid} has an invalid label"
    if kind is None:
        kind = "text"
    if kind not in QUESTION_TYPES:
        return f"question {qid} has an unknown type"
    return Question(qid, match, None, label, kind)


def parse_questions(raw: object) -> tuple[Question, ...] | str:
    """The questions, or why they were refused. Missing or null reads as none."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return "questions are not a list"
    if len(raw) > MAX_QUESTIONS:
        return f"more than {MAX_QUESTIONS} questions"
    found: list[Question] = []
    for item in raw:
        result = _question(item)
        if isinstance(result, str):
            return result
        if any(q.id == result.id for q in found):
            return f"question {result.id} is listed twice"
        found.append(result)
    return tuple(found)


def _login(raw: object) -> Login | str:
    if not isinstance(raw, dict):
        return "has an invalid login"
    command = raw.get("command")
    if not is_command(command):
        return "has an invalid login command"
    markers = []
    for key in ("link_after", "code_after"):
        value = raw.get(key)
        if value is not None and not _text(value, 1, MAX_MARKER):
            return f"has an invalid login {key}"
        markers.append(value)
    return Login(command, markers[0], markers[1])


def _connection(raw: object) -> Connection | str:
    """The connection, or why it was refused."""
    if not isinstance(raw, dict):
        return "is not an object"
    cid, name, description = raw.get("id"), raw.get("name"), raw.get("description")
    if not is_slug(cid):
        return "has an invalid id"
    if not _text(name, 1, MAX_NAME):
        return "has an invalid name"
    if description is not None and not _text(description, 0, MAX_DESCRIPTION):
        return "has an invalid description"
    login = None
    if raw.get("login") is not None:
        login = _login(raw["login"])
        if isinstance(login, str):
            return login
    check = raw.get("check")
    if check is not None and not is_command(check):
        return "has an invalid check"
    questions = parse_questions(raw.get("questions"))
    if isinstance(questions, str):
        return f"has invalid questions ({questions})"
    if login is None and not questions:
        return "has neither a login nor questions"
    return Connection(cid, name, description, login, check, questions)


def parse_connections(payload: object) -> tuple[dict[str, Connection], set[str]]:
    """Valid connections by id from the service's body, and the ids of entries refused.
    A repeated id keeps its first entry."""
    entries = payload.get("connections") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return {}, set()
    found: dict[str, Connection] = {}
    rejected: set[str] = set()
    for raw in entries:
        result = _connection(raw)
        if isinstance(result, str):
            if isinstance(raw, dict) and is_slug(raw.get("id")):
                rejected.add(raw["id"])
        elif result.id not in found:
            found[result.id] = result
    return found, rejected
