from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

import yaml

from .detect import detect_web, WebSpec
from .overlay import build_overlay

STARTED_OK = "started_ok"
FAILED_TO_START = "failed_to_start"
CRASH_LOOPING = "crash_looping"


@dataclass(frozen=True)
class Project:
    id: str
    webs: list[WebSpec]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")


def load_project(compose_dict: dict, project_yml: dict | None, dir_name: str) -> Project:
    project_yml = project_yml or {}
    pid = _slug(project_yml["id"]) if project_yml.get("id") else _slug(dir_name)
    if project_yml.get("web"):
        webs = [WebSpec(service=w["service"], port=int(w["port"]),
                        subdomain=w.get("subdomain"))
                for w in project_yml["web"]]
    else:
        webs = detect_web(compose_dict)
    if len(webs) > 1:
        webs = [webs[0]] + [
            replace(w, subdomain=w.subdomain or w.service) for w in webs[1:]
        ]
    return Project(id=pid, webs=webs)


def overlay_yaml(project: Project, domain: str) -> str:
    overlay = build_overlay(project.id, project.webs, domain)
    return yaml.safe_dump(overlay, sort_keys=False)


def _parse_ps_rows(ps_json: str) -> list[dict]:
    text = ps_json.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows


def classify(up_result, ps_json: str) -> str:
    if not up_result.ok:
        return FAILED_TO_START
    rows = _parse_ps_rows(ps_json)
    for row in rows:
        state = str(row.get("State", "")).lower()
        if state == "restarting" or (state == "exited" and row.get("ExitCode", 0) != 0):
            return CRASH_LOOPING
    return STARTED_OK
