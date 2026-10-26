"""Rules for pushing a local project to an instance: what travels, what counts as data, how secrets merge. No I/O."""
from __future__ import annotations

import posixpath
from dataclasses import dataclass

from ..errors import Invalid
from .project import _slug
from .secrets import SecretError, check_name

EXCLUDED_DIRS = frozenset({".git", "node_modules", ".venv", "__pycache__"})
EXCLUDED_FILES = frozenset({".eggie/overlay.yml", ".DS_Store"})

DATABASE_IMAGES = frozenset({
    "postgres", "postgis", "mysql", "mariadb", "mongo", "redis", "valkey", "elasticsearch",
    "opensearch", "clickhouse-server", "couchdb", "influxdb", "neo4j"})

REPLACE_NONE = "none"
REPLACE_FILES = "files"
REPLACE_ALL = "files-and-data"

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
class Database:
    service: str
    image: str
    floating: bool


def databases(compose: dict) -> list[Database]:
    found = []
    for name, svc in sorted((compose.get("services") or {}).items()):
        image = (svc or {}).get("image")
        if not isinstance(image, str):
            continue
        repo, _ = _split_image(image)
        if repo.rsplit("/", 1)[-1] in DATABASE_IMAGES:
            found.append(Database(name, image, is_floating(image)))
    return found


@dataclass(frozen=True)
class DataPlan:
    volumes: tuple[str, ...]
    data_paths: tuple[str, ...]
    external: tuple[str, ...]


def _mounts(svc: dict):
    """Yield (kind, source, read_only) for each mount, kind being 'bind' or 'volume'."""
    for entry in svc.get("volumes") or []:
        if isinstance(entry, str):
            parts = entry.split(":")
            if len(parts) < 2:
                continue
            src = parts[0]
            options = parts[2].split(",") if len(parts) > 2 else []
            yield ("bind" if src.startswith((".", "/")) else "volume"), src, "ro" in options
        elif isinstance(entry, dict) and isinstance(entry.get("source"), str):
            if entry.get("type") in ("bind", "volume"):
                yield entry["type"], entry["source"], entry.get("read_only") is True


def data_plan(compose: dict) -> DataPlan:
    declared = compose.get("volumes") or {}
    used: set[str] = set()
    paths: set[str] = set()
    db_services = {d.service for d in databases(compose)}
    for name, svc in (compose.get("services") or {}).items():
        for kind, src, read_only in _mounts(svc or {}):
            if kind == "volume":
                used.add(src)
            # A database never writes through a read-only bind: that is config.
            elif name in db_services and not read_only:
                path = posixpath.normpath(src)
                if path != "." and path != ".." and not path.startswith(("../", "/")):
                    paths.add(path)
    external = {k for k in used if k in declared and (declared[k] or {}).get("external")}
    volumes = {k for k in used if k in declared} - external
    return DataPlan(tuple(sorted(volumes)), tuple(sorted(paths)), tuple(sorted(external)))


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


def replace_mode(pushed_before: bool, data: bool) -> str:
    if not pushed_before:
        return REPLACE_NONE
    return REPLACE_ALL if data else REPLACE_FILES


def check_replace(exists: bool, data: bool, replace: str) -> str | None:
    if not exists:
        return None
    if replace == REPLACE_NONE:
        return "already_on_instance"
    if data and replace != REPLACE_ALL:
        return "confirm_replace_data"
    return None


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


def build_manifest(*, data: bool, plan: DataPlan, secrets: dict[str, str]) -> dict:
    return {
        "format": MANIFEST_FORMAT,
        "data": data,
        "volumes": list(plan.volumes),
        "data_paths": list(plan.data_paths),
        "secrets": [{"name": n, "hint": secrets[n]} for n in sorted(secrets)],
    }


@dataclass(frozen=True)
class Manifest:
    data: bool
    volumes: tuple[str, ...]
    data_paths: tuple[str, ...]
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
    data = raw.get("data")
    if not isinstance(data, bool):
        raise _bad("manifest data must be true or false")
    volumes, paths = _str_list(raw, "volumes"), _str_list(raw, "data_paths")
    plan = data_plan(compose)
    if not set(volumes) <= set(plan.volumes) or not set(paths) <= set(plan.data_paths):
        raise _bad("manifest names data the compose file does not have")
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
    return Manifest(data, tuple(volumes), tuple(paths), secrets)
