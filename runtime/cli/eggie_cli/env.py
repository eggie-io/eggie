from __future__ import annotations

import getpass as _getpass
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TextIO

from .api import ApiClient, default_client
from .constants import GUEST_PROJECTS
from .errors import EggieError
from .project import docker_gid, project_of, require_id


def run_git(argv: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(argv, env=env, capture_output=True, text=True)


@dataclass
class Env:
    """Everything a command touches besides its arguments, so tests can point
    it at a temporary projects root and an in-process client."""
    root: Path = Path(GUEST_PROJECTS)
    cwd: Path = field(default_factory=Path.cwd)
    client: Callable[[], ApiClient] = default_client
    gid: Callable[[], int] = docker_gid
    git: Callable[[list[str], dict], subprocess.CompletedProcess] = run_git
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    getpass: Callable[[str], str] = _getpass.getpass


def project_here(env: Env, directory: str | None) -> tuple[Path, str] | None:
    folder = project_of(env.cwd / directory if directory else env.cwd, env.root)
    return (folder, require_id(folder)) if folder else None


def require_project(env: Env) -> str:
    found = project_here(env, None)
    if found is None:
        raise EggieError("Run this inside a project folder in "
                          f"~/projects ({GUEST_PROJECTS}).")
    return found[1]
