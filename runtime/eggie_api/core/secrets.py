"""Project secrets: what a name and value may be, which names a project
expects, and reading a `.env` file. No storage, no FastAPI."""
from __future__ import annotations

import re

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# compose and the docker CLI read these from their own environment, and
# secrets are that environment.
RESERVED_PREFIXES = ("COMPOSE_", "DOCKER_")
MAX_VALUE_BYTES = 64 * 1024
MAX_PROJECT_BYTES = 512 * 1024

_DEFAULTED = {"-", ":-", "+", ":+"}
_BRACED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?[-+?])?[^}]*\}")
_BARE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_ESCAPES = {"n": "\n", '"': '"', "\\": "\\"}


class SecretError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def check_name(name: str) -> None:
    if not NAME.fullmatch(name):
        raise SecretError("secret_name_invalid",
                          f"'{name}' can't be a secret name: use letters, digits "
                          "and underscores, not starting with a digit")
    if name.upper().startswith(RESERVED_PREFIXES):
        raise SecretError("secret_name_reserved",
                          f"'{name}' is reserved: names starting with COMPOSE_ "
                          "or DOCKER_ would change how Eggie runs the project")


def check_value(value: str) -> None:
    if "\x00" in value:
        raise SecretError("secret_invalid_value",
                          "a secret's value can't contain a NUL character")
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        raise SecretError("secret_too_large",
                          f"a secret's value can be at most {MAX_VALUE_BYTES // 1024} KB")


def check_total(values: dict[str, str]) -> None:
    total = sum(len(n.encode("utf-8")) + len(v.encode("utf-8")) + 2
                for n, v in values.items())
    if total > MAX_PROJECT_BYTES:
        raise SecretError("secrets_too_large",
                          f"a project's secrets can add up to at most "
                          f"{MAX_PROJECT_BYTES // 1024} KB")


def _example_keys(text: str) -> set[str]:
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, sep, _ = line.partition("=")
        if sep and NAME.fullmatch(key.strip()):
            keys.add(key.strip())
    return keys


def _compose_refs(text: str) -> set[str]:
    refs = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        # `$$` is compose's escape for a literal dollar sign.
        line = line.replace("$$", "")
        for match in _BRACED.finditer(line):
            if match.group(2) not in _DEFAULTED:
                refs.add(match.group(1))
        for match in _BARE.finditer(_BRACED.sub("", line)):
            refs.add(match.group(1))
    return refs


def expected(example_text: str | None, compose_text: str | None) -> set[str]:
    return ((_example_keys(example_text) if example_text else set())
            | (_compose_refs(compose_text) if compose_text else set()))


def _closing(body: str, quote: str) -> int:
    i = 0
    while i < len(body):
        if quote == '"' and body[i] == "\\":
            i += 2
            continue
        if body[i] == quote:
            return i
        i += 1
    return -1


def _unescape(body: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), body,
                  flags=re.DOTALL)


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        number = i + 1
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, sep, rest = line.partition("=")
        key = key.strip()
        if not sep:
            raise SecretError("dotenv_invalid",
                              f".env line {number} isn't NAME=value")
        # An invalid key is often a piece of an unquoted secret, so its
        # text must not reach the error message.
        if not NAME.fullmatch(key):
            raise SecretError("dotenv_invalid",
                              f".env line {number} has an invalid name")
        check_name(key)
        rest = rest.lstrip()
        if rest[:1] in ("'", '"'):
            quote, body = rest[0], rest[1:]
            end = _closing(body, quote)
            while end == -1:
                if i >= len(lines):
                    raise SecretError("dotenv_invalid",
                                      f".env line {number} opens a quote "
                                      "that is never closed")
                body += "\n" + lines[i]
                i += 1
                end = _closing(body, quote)
            value = _unescape(body[:end]) if quote == '"' else body[:end]
        else:
            value = re.split(r"\s+#", rest, maxsplit=1)[0].strip()
        check_value(value)
        values[key] = value
    return values
