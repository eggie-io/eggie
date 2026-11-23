"""The sign-up events the console may relay. The VM is never an open relay:
anything outside these two events and their exact shapes is refused."""
from __future__ import annotations

import re

from ..errors import BadRequest

_SESSION_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_CONTEXT = {
    "app_version": re.compile(r"[0-9A-Za-z.+-]{1,32}"),
    "os": re.compile(r"windows|macos"),
    "os_version": re.compile(r"[0-9A-Za-z._-]{1,32}"),
    "arch": re.compile(r"[0-9A-Za-z_-]{1,16}"),
}
_METHODS = ("new", "existing")


def _refuse(why: str):
    raise BadRequest("invalid", f"not a sign-up event: {why}")


def _matches(pattern: re.Pattern, value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def parse(body: object) -> dict:
    if not isinstance(body, dict) or not {"event", "session_id", "context"} <= body.keys() \
            or not body.keys() <= {"event", "session_id", "properties", "context"}:
        _refuse("unexpected fields")
    if not _matches(_SESSION_ID, body["session_id"]):
        _refuse("session_id")
    context = body["context"]
    if not isinstance(context, dict) or context.keys() != _CONTEXT.keys() \
            or not all(_matches(_CONTEXT[k], v) for k, v in context.items()):
        _refuse("context")
    properties = body.get("properties", {})
    if body["event"] == "signup_screen_shown" and properties == {}:
        properties = {}
    elif body["event"] == "signup_started" and isinstance(properties, dict) \
            and properties.keys() == {"method"} and properties["method"] in _METHODS:
        properties = {"method": properties["method"]}
    else:
        _refuse("event or properties")
    return {"event": body["event"], "session_id": body["session_id"],
            "properties": properties, "context": dict(context)}
