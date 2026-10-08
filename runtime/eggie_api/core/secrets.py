"""Project secrets: what a name and value may be, which names a project
expects, and reading a `.env` file. No storage, no FastAPI."""
from __future__ import annotations

import re

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Compose, the docker CLI and the dynamic loader read these from their own
# environment, and secrets are that environment.
RESERVED_PREFIXES = ("COMPOSE_", "DOCKER_", "LD_")
RESERVED_NAMES = frozenset({"PATH", "HOME"})
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


def is_reserved(name: str) -> bool:
    upper = name.upper()
    return upper in RESERVED_NAMES or upper.startswith(RESERVED_PREFIXES)


def check_name(name: str) -> None:
    if not NAME.fullmatch(name):
        raise SecretError("secret_name_invalid",
                          f"'{name}' can't be a secret name: use letters, digits "
                          "and underscores, not starting with a digit")
    if is_reserved(name):
        raise SecretError("secret_name_reserved",
                          f"'{name}' is reserved: Eggie and Docker read it "
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


def check_total(values: dict[str, str]) -> None:
    total = sum(len(n.encode("utf-8")) + len(v.encode("utf-8")) + 2
                for n, v in values.items())
    if total > MAX_PROJECT_BYTES:
        raise SecretError("secrets_too_large",
                          f"a project's secrets can add up to at most "
                          f"{MAX_PROJECT_BYTES // 1024} KB")


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


def _entries(text: str):
    """Yield (line number, (key, value)) or (line number, SecretError) per
    entry; stops after an unclosed quote."""
    lines = text.removeprefix("\ufeff").splitlines()
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
            yield number, SecretError("dotenv_invalid",
                                      f".env line {number} isn't NAME=value")
            continue
        # An invalid key is often a piece of an unquoted secret, so its
        # text must not reach the error message.
        if not NAME.fullmatch(key):
            yield number, SecretError("dotenv_invalid",
                                      f".env line {number} has an invalid name")
            continue
        rest = rest.lstrip()
        if rest[:1] in ("'", '"'):
            quote, body = rest[0], rest[1:]
            end = _closing(body, quote)
            while end == -1:
                if i >= len(lines):
                    yield number, SecretError(
                        "dotenv_invalid",
                        f".env line {number} opens a quote that is never closed")
                    return
                body += "\n" + lines[i]
                i += 1
                end = _closing(body, quote)
            value = _unescape(body[:end]) if quote == '"' else body[:end]
        elif rest.startswith("#"):
            value = ""
        else:
            value = re.split(r"\s+#", rest, maxsplit=1)[0].strip()
        yield number, (key, value)


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for _number, entry in _entries(text):
        if isinstance(entry, SecretError):
            raise entry
        key, value = entry
        check_value(value)
        values[key] = value
    return values


def parse_example(text: str | None) -> dict[str, str]:
    """`.env.example` lists a project's variables; a line that doesn't parse
    must never stop a start, so it is skipped."""
    values: dict[str, str] = {}
    for _number, entry in _entries(text or ""):
        if isinstance(entry, SecretError):
            continue
        key, value = entry
        try:
            check_value(value)
        except SecretError:
            continue
        if not is_reserved(key):
            values[key] = value
    return values


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
        # `X: ${X}` or a bare `X` takes its value from Eggie's environment;
        # only a literal is the project's own choice.
        names[service] = {k for k, v in pairs
                          if v is not None and "$" not in str(v)}
    return list(services), names


def defaults(example: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in example.items() if v != "" and not is_reserved(k)}


def missing(example: dict[str, str], compose_text: str | None, *,
            have: set[str], declared_names: set[str]) -> list[str]:
    wanted = ({k for k, v in example.items() if v == ""}
              | (_compose_refs(compose_text) if compose_text else set()))
    taken = have | set(defaults(example)) | declared_names
    return sorted(n for n in wanted - taken if not is_reserved(n))


def importable(values: dict[str, str], defaults_: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in values.items()
            if v != "" and not is_reserved(k) and v != defaults_.get(k)}
