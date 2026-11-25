"""The coding-agent guides that ship with the app (`ui/agents/`).

`index.json` orders the agents; `<id>/agent.json` holds one agent's name,
download page, automatic connect kind and the steps shown once it is
connected, per OS and kitchen kind. Everything is checked on load: a body
is HTML the page builds into elements, so only a short tag list passes.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

OSES = ("mac", "windows")
KINDS = ("local", "cloud")
CONNECT_KINDS = ("claude", "codex", None)
ALLOWED_TAGS = frozenset({"p", "ul", "ol", "li", "strong", "em", "b", "i", "code", "kbd", "br"})
TEMPLATES = frozenset({"{ssh_host}", "{kitchen}"})

_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_FILE_RE = re.compile(r"[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*")
_TEMPLATE_RE = re.compile(r"\{[^{}]*\}")


@dataclass(frozen=True)
class Step:
    title: str
    body: str
    alt: str
    # Paths relative to ui/, as the page loads them.
    media: tuple[str, ...] = ()


@dataclass(frozen=True)
class Confirm:
    """What the person agrees to before a connection switches a system default."""
    title: str
    body: str
    button: str


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    icon: str
    get_url: str
    connect: str | None
    steps: dict  # {os: {kind: tuple[Step, ...]}}
    confirm: dict = field(default_factory=dict)  # {os: Confirm}, only where one applies


class _Body(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.error = ""

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS or attrs:
            self.error = self.error or f"<{tag}> with {len(attrs)} attribute(s)"
        elif tag != "br":
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag != "br" or attrs:
            self.error = self.error or f"<{tag}/>"

    def handle_endtag(self, tag):
        if tag == "br":
            return
        if not self.open or self.open[-1] != tag:
            self.error = self.error or f"</{tag}> closes nothing open"
        else:
            self.open.pop()

    def handle_comment(self, data):
        self.error = self.error or "a comment"

    def handle_decl(self, decl):
        self.error = self.error or "a declaration"

    def handle_pi(self, data):
        self.error = self.error or "a processing instruction"


def _check_body(body: str) -> None:
    parser = _Body()
    parser.feed(body)
    parser.close()
    if parser.error:
        raise ValueError(f"body has {parser.error}")
    if parser.open:
        raise ValueError(f"body leaves <{parser.open[-1]}> open")


def _check_templates(text: str) -> None:
    unknown = set(_TEMPLATE_RE.findall(text)) - TEMPLATES
    if unknown:
        raise ValueError(f"unknown template {sorted(unknown)[0]}")


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    return value


def _file(folder: Path, agent_id: str, path: object) -> str:
    if not isinstance(path, str) or not _FILE_RE.fullmatch(path) or ".." in path.split("/"):
        raise ValueError(f"'{path}' is not a file in the agent's folder")
    if not (folder / path).is_file():
        raise ValueError(f"'{path}' is missing")
    return f"agents/{agent_id}/{path}"


def _step(folder: Path, agent_id: str, data: object) -> Step:
    if not isinstance(data, dict):
        raise ValueError("a step must be an object")
    title, body, alt = _text(data, "title"), _text(data, "body"), _text(data, "alt")
    for text in (title, body, alt):
        _check_templates(text)
    _check_body(body)
    media = data.get("media", [])
    if not isinstance(media, list):
        raise ValueError("'media' must be a list")
    return Step(title, body, alt, tuple(_file(folder, agent_id, m) for m in media))


def _manifest(folder: Path, agent_id: str) -> Manifest:
    data = json.loads((folder / "agent.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("agent.json must hold an object")
    get_url = _text(data, "get_url")
    if not get_url.startswith("https://"):
        raise ValueError("'get_url' must be an https:// address")
    connect = data.get("connect")
    if connect not in CONNECT_KINDS:
        raise ValueError(f"'connect' must be one of {CONNECT_KINDS}")
    steps_in = data.get("steps")
    steps = {}
    for os_name in OSES:
        steps[os_name] = {}
        for kind in KINDS:
            listed = (steps_in or {}).get(os_name, {}).get(kind) if isinstance(steps_in, dict) else None
            if not isinstance(listed, list) or not listed:
                raise ValueError(f"no steps for {os_name} {kind}")
            steps[os_name][kind] = tuple(_step(folder, agent_id, s) for s in listed)
    return Manifest(id=agent_id, name=_text(data, "name"),
                    icon=_file(folder, agent_id, data.get("icon")), get_url=get_url,
                    connect=connect, steps=steps, confirm=_confirms(data.get("confirm", {})))


def _confirms(data: object) -> dict:
    if not isinstance(data, dict) or not set(data) <= set(OSES):
        raise ValueError(f"'confirm' must map some of {OSES} to an object")
    confirms = {}
    for os_name, entry in data.items():
        if not isinstance(entry, dict):
            raise ValueError(f"'confirm' for {os_name} must be an object")
        title, body, button = (_text(entry, k) for k in ("title", "body", "button"))
        if any(_TEMPLATE_RE.search(t) for t in (title, body, button)):
            raise ValueError("'confirm' takes no templates")
        _check_body(body)
        confirms[os_name] = Confirm(title, body, button)
    return confirms


def load(ui_dir: Path) -> list[Manifest]:
    """Every agent in index.json's order. Raises ValueError naming the agent
    and the first thing wrong with it."""
    root = Path(ui_dir) / "agents"
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    ids = index.get("agents") if isinstance(index, dict) else None
    if not isinstance(ids, list):
        raise ValueError("agents/index.json must list 'agents'")
    manifests = []
    for agent_id in ids:
        if not isinstance(agent_id, str) or not _ID_RE.fullmatch(agent_id):
            raise ValueError(f"'{agent_id}' is not an agent id")
        try:
            manifests.append(_manifest(root / agent_id, agent_id))
        except (OSError, ValueError) as e:
            raise ValueError(f"agents/{agent_id}: {e}") from None
    return manifests


def _fill(text: str, values: dict[str, str]) -> str:
    # One pass, so a kitchen named "{ssh_host}" stays as typed.
    return _TEMPLATE_RE.sub(lambda m: values.get(m.group(0), m.group(0)), text)


def render_steps(manifest: Manifest, os: str, kind: str, ssh_host: str,
                 kitchen: str) -> list[dict]:
    """The steps for this OS and kitchen kind, filled in. Values are escaped in
    the HTML bodies only; titles and alt text are drawn as text."""
    if os not in OSES or kind not in KINDS:
        raise ValueError(f"no steps for {os} {kind}")
    plain = {"{ssh_host}": ssh_host, "{kitchen}": kitchen}
    escaped = {key: html.escape(value) for key, value in plain.items()}
    return [{"title": _fill(s.title, plain), "body": _fill(s.body, escaped),
             "alt": _fill(s.alt, plain), "media": list(s.media)}
            for s in manifest.steps[os][kind]]
