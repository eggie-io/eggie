"""Project secrets: what a name, value and request hint may be, and which names a compose service sets itself. No storage, no FastAPI."""
from __future__ import annotations

import base64
import binascii
import re

from ..errors import BadRequest

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Compose, the docker CLI and the dynamic loader read these from their own
# environment, and secrets are that environment.
RESERVED_PREFIXES = ("COMPOSE_", "DOCKER_", "LD_", "BUILDX_", "BUILDKIT_")
RESERVED_NAMES = frozenset({"PATH", "HOME"})
MAX_VALUE_BYTES = 64 * 1024
MAX_PROJECT_BYTES = 512 * 1024

MAX_HINT_CHARS = 500
MAX_FILENAME_CHARS = 200
KINDS = ("text", "file")


class SecretError(BadRequest):
    def __init__(self, code: str, message: str):
        super().__init__(code, message)


def is_reserved(name: str) -> bool:
    upper = name.upper()
    return upper in RESERVED_NAMES or upper.startswith(RESERVED_PREFIXES)


def check_name(name: str) -> None:
    if not NAME.fullmatch(name):
        raise SecretError("secret_name_invalid",
                          "a secret's name can use letters, digits and underscores, "
                          "and can't start with a digit")
    if is_reserved(name):
        raise SecretError("secret_name_reserved",
                          "that name is reserved: Eggie and Docker read it "
                          "from their own environment")


def check_value(value: str) -> None:
    if "\x00" in value:
        raise SecretError("secret_invalid_value",
                          "a secret's value can't contain a NUL character")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise SecretError("secret_invalid_value",
                          "a secret's value can't contain unpaired surrogate "
                          "characters") from None
    if size > MAX_VALUE_BYTES:
        raise SecretError("secret_too_large",
                          f"a secret's value can be at most {MAX_VALUE_BYTES // 1024} KB")


def check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise SecretError("secret_kind_invalid",
                          "a secret's kind is either text or file")


def decode_file(value: str) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise SecretError("secret_invalid_value",
                          "a file secret must be base64") from None
    if not data:
        raise SecretError("secret_invalid_value", "a file can't be empty")
    if len(data) > MAX_VALUE_BYTES:
        raise SecretError("secret_too_large",
                          f"a file can be at most {MAX_VALUE_BYTES // 1024} KB")
    return data


def check_filename(name: str) -> None:
    ok = (0 < len(name) <= MAX_FILENAME_CHARS
          and "/" not in name and "\\" not in name
          and not any(c < " " or c == "\x7f" for c in name))
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        ok = False
    if not ok:
        raise SecretError("secret_filename_invalid",
                          f"a file name is one line of plain text, 1 to "
                          f"{MAX_FILENAME_CHARS} characters, without / or \\")


def stored_size(value: str, kind: str) -> int:
    if kind == "file":
        return len(base64.b64decode(value))
    return len(value.encode("utf-8"))


def check_total(entries: dict[str, tuple[str, str]]) -> None:
    total = sum(len(n.encode("utf-8")) + stored_size(v, k) + 2
                for n, (v, k) in entries.items())
    if total > MAX_PROJECT_BYTES:
        raise SecretError("secrets_too_large",
                          f"a project's secrets can add up to at most "
                          f"{MAX_PROJECT_BYTES // 1024} KB")


def declared(compose: dict) -> tuple[list[str], dict[str, set[str]]]:
    services = compose.get("services")
    if not isinstance(services, dict):
        return [], {}
    names: dict[str, set[str]] = {}
    for service, spec in services.items():
        env = spec.get("environment") if isinstance(spec, dict) else None
        if isinstance(env, dict):
            pairs = [(str(k), v) for k, v in env.items()]
        elif isinstance(env, list):
            pairs = [(k, v) for k, sep, v in
                     (str(e).partition("=") for e in env) if sep]
        else:
            pairs = []
        # `X: ${X}`, a bare `X` or an empty `X: ""` is a placeholder that
        # takes Eggie's value; only a non-empty literal is the project's own.
        names[service] = {k for k, v in pairs
                          if v is not None and str(v) != "" and "$" not in str(v)}
    return list(services), names


def check_hint(hint: str) -> None:
    ok = (0 < len(hint) <= MAX_HINT_CHARS
          and not any(c != "\t" and (c < " " or c == "\x7f") for c in hint))
    try:
        hint.encode("utf-8")
    except UnicodeEncodeError:
        ok = False
    if not ok:
        raise SecretError("secret_hint_invalid",
                          f"a hint is one line of plain text, 1 to {MAX_HINT_CHARS} characters")
