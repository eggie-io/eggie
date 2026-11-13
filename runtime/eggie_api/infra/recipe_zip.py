"""Unpacks a recipe bundle. Every entry is checked from the zip's central
directory before anything is written, so a refused bundle leaves nothing."""
from __future__ import annotations

import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

RECIPE_MD = "RECIPE.md"
# Group-writable like pushed project files: login users are never the API's
# uid, and an explicit chmod would narrow the projects folder's default ACL.
FILE_MODE, EXEC_MODE, DIR_MODE = 0o664, 0o775, 0o2775


class BadBundle(Exception):
    pass


def _check(info: zipfile.ZipInfo) -> tuple[PurePosixPath, bool, bool]:
    """(relative path, is a folder, is executable) or BadBundle."""
    name = info.filename
    path = PurePosixPath(name)
    if (not name or "\\" in name or path.is_absolute()
            or any(part in ("..", "") for part in name.rstrip("/").split("/"))):
        raise BadBundle(f"the recipe has a file outside its folder: {name!r}")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    is_dir = name.endswith("/") or kind == stat.S_IFDIR
    # 0: no Unix mode recorded (a zip made on Windows); a plain file.
    if not is_dir and kind not in (0, stat.S_IFREG):
        raise BadBundle(f"the recipe has a link or special file: {name!r}")
    return path, is_dir, bool(mode & 0o111)


def unpack(zip_path: Path, dest: Path, *, max_files: int, max_unpacked_bytes: int) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if len(infos) > max_files:
                raise BadBundle(f"the recipe has more than {max_files} files")
            if sum(i.file_size for i in infos) > max_unpacked_bytes:
                raise BadBundle(f"the recipe unpacks to more than {max_unpacked_bytes} bytes")
            checked = [(info, *_check(info)) for info in infos]
            if not any(str(path) == RECIPE_MD and not is_dir
                       for _info, path, is_dir, _x in checked):
                raise BadBundle(f"the recipe has no {RECIPE_MD} at its root")
            dest.mkdir()
            try:
                for info, path, is_dir, executable in checked:
                    target = dest.joinpath(*path.parts)
                    if is_dir:
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                    os.chmod(target, EXEC_MODE if executable else FILE_MODE)
                for folder in [dest, *(p for p in dest.rglob("*") if p.is_dir())]:
                    os.chmod(folder, DIR_MODE)
            except BaseException:
                shutil.rmtree(dest, ignore_errors=True)
                raise
    except zipfile.BadZipFile as e:
        raise BadBundle(f"the recipe download is not a valid zip ({e})") from None
    except (zipfile.LargeZipFile, EOFError, ValueError) as e:
        raise BadBundle(f"the recipe download is damaged ({e})") from None
