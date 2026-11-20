"""File secrets on disk, where compose reads them to mount into containers."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def remove(root: Path, project_id: str) -> None:
    shutil.rmtree(root / project_id, ignore_errors=True)


def write_all(root: Path, project_id: str, files: dict[str, bytes]) -> Path:
    folder = root / project_id
    remove(root, project_id)
    root.mkdir(parents=True, exist_ok=True)
    os.mkdir(folder, 0o700)
    os.chmod(folder, 0o700)
    for name, data in files.items():
        path = folder / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(path, 0o444)
    return folder
