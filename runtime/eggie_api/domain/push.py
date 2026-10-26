"""Rules for pushing a local project to an instance: what travels, how a repeat
push replaces, how secrets merge. No I/O."""
from __future__ import annotations

from dataclasses import dataclass

from ..errors import Invalid
from .project import _slug
from .secrets import SecretError, check_name

EXCLUDED_DIRS = frozenset({".git", "node_modules", ".venv", "__pycache__"})
EXCLUDED_FILES = frozenset({".eggie/overlay.yml", ".DS_Store"})

REPLACE_NONE = "none"
REPLACE_ALL = "all"

DEFAULT_HINT = "Set on your computer. Enter its value for the kitchen."

MANIFEST_FORMAT = 1
MAX_RETURN_PATH = 1024


def is_excluded(rel: str) -> bool:
    parts = rel.split("/")
    return rel in EXCLUDED_FILES or any(p in EXCLUDED_DIRS for p in parts) \
        or parts[-1] in EXCLUDED_FILES


def _split_image(image: str) -> tuple[str, str | None]:
    """(repository, tag) with any digest dropped."""
    head, sep, tail = image.split("@", 1)[0].rpartition(":")
    # A colon followed by a slash is a registry port, not a tag.
    if sep and "/" not in tail:
        return head, tail
    return image.split("@", 1)[0], None


def is_floating(image: str) -> bool:
    if "@" in image:
        return False
    _, tag = _split_image(image)
    return tag is None or not any(c.isdigit() for c in tag)


@dataclass(frozen=True)
class DataPlan:
    volumes: tuple[str, ...]
    external: tuple[str, ...]


def _named_volumes(svc: dict) -> set[str]:
    """Sources of the service's named-volume mounts; binds are project files."""
    found = set()
    for entry in svc.get("volumes") or []:
        if isinstance(entry, str):
            src, sep, _ = entry.partition(":")
            if sep and not src.startswith((".", "/", "~")):
                found.add(src)
        elif isinstance(entry, dict) and entry.get("type") == "volume" \
                and isinstance(entry.get("source"), str):
            found.add(entry["source"])
    return found


def data_plan(compose: dict) -> DataPlan:
    declared = compose.get("volumes") or {}
    used = {k for svc in (compose.get("services") or {}).values()
            for k in _named_volumes(svc or {}) if k in declared}
    external = {k for k in used if (declared[k] or {}).get("external")}
    return DataPlan(tuple(sorted(used - external)), tuple(sorted(external)))


@dataclass(frozen=True)
class Floating:
    service: str
    image: str


def floating(compose: dict) -> list[Floating]:
    """Services whose data travels but whose image may be a newer major version
    on the instance, one that cannot read it."""
    copied = set(data_plan(compose).volumes)
    found = []
    for name, svc in sorted((compose.get("services") or {}).items()):
        image = (svc or {}).get("image")
        if isinstance(image, str) and is_floating(image) and _named_volumes(svc) & copied:
            found.append(Floating(name, image))
    return found


def docker_volume_name(compose: dict, compose_name: str, key: str) -> str:
    spec = (compose.get("volumes") or {}).get(key) or {}
    return spec.get("name") or f"{compose_name}_{key}"


def volume_outside(compose: dict, compose_name: str,
                   keys) -> tuple[str, str] | None:
    """(key, docker name) of the first volume an instance refuses: restoring removes
    each volume first, so it only takes names under the project's compose name."""
    for key in keys:
        name = docker_volume_name(compose, compose_name, key)
        if not name.startswith(f"{compose_name}_"):
            return key, name
    return None


def check_replace(exists: bool, replace: str) -> str | None:
    return "already_on_instance" if exists and replace == REPLACE_NONE else None


@dataclass(frozen=True)
class SecretMerge:
    request: dict[str, str]
    keep: tuple[str, ...]
    unused: tuple[str, ...]
    drop_requests: tuple[str, ...]


def merge_secret_names(pushed: dict[str, str], stored: set[str], requested: set[str]) -> SecretMerge:
    names = set(pushed)
    return SecretMerge(
        request={n: pushed[n] or DEFAULT_HINT for n in sorted(names - stored)},
        keep=tuple(sorted(names & stored)),
        unused=tuple(sorted(stored - names)),
        drop_requests=tuple(sorted(requested - names)))


def pinned_project_yml(project_yml: dict, project_id: str) -> dict | None:
    """project.yml with its id set to the instance's, or None when nothing
    needs writing: an id there would otherwise name the project."""
    current = project_yml.get("id")
    if not current or _slug(str(current)) == project_id:
        return None
    return {**project_yml, "id": project_id}


def instance_project_id(name: str, taken: set[str]) -> str:
    base = _slug(name) or "project"
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def safe_return_path(path: object) -> str | None:
    if not isinstance(path, str) or not path or len(path) > MAX_RETURN_PATH or path[0] != "/":
        return None
    if len(path) > 1 and path[1] in "/\\":
        return None
    if any(c == "\\" or c == "#" or c.isspace() or c < " " or c == "\x7f" for c in path):
        return None
    return path


def build_manifest(*, volumes, secrets: dict[str, str]) -> dict:
    return {
        "format": MANIFEST_FORMAT,
        "volumes": sorted(volumes),
        "secrets": [{"name": n, "hint": secrets[n]} for n in sorted(secrets)],
    }


@dataclass(frozen=True)
class Manifest:
    volumes: tuple[str, ...]
    secrets: dict[str, str]


def _bad(message: str) -> Invalid:
    return Invalid("bad_manifest", message)


def _str_list(raw: dict, key: str) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _bad(f"manifest {key} must be a list of strings")
    return value


def parse_manifest(raw: object, compose: dict) -> Manifest:
    if not isinstance(raw, dict) or raw.get("format") != MANIFEST_FORMAT:
        raise _bad("unsupported manifest format")
    volumes = _str_list(raw, "volumes")
    if not set(volumes) <= set(data_plan(compose).volumes):
        raise _bad("manifest names volumes the compose file does not have")
    entries = raw.get("secrets")
    if not isinstance(entries, list):
        raise _bad("manifest secrets must be a list")
    secrets: dict[str, str] = {}
    for entry in entries:
        if not (isinstance(entry, dict) and isinstance(entry.get("name"), str)
                and isinstance(entry.get("hint"), str)):
            raise _bad("each manifest secret needs a name and a hint")
        try:
            check_name(entry["name"])
        except SecretError as e:
            raise _bad(e.message) from e
        if entry["name"] in secrets:
            raise _bad(f"secret {entry['name']} listed twice")
        secrets[entry["name"]] = entry["hint"]
    return Manifest(tuple(volumes), secrets)
