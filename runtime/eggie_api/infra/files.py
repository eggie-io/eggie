from __future__ import annotations

import os
import posixpath
import tarfile
from pathlib import Path, PurePosixPath

from ..errors import BadRequest

# No size cap here on an uploaded archive or a single file: removing the old
# ~24 KB command-line ceiling is this module's entire purpose. The runaway/
# abuse cap lives where the bytes are streamed in, rest/routers/files.py's
# `stream_to_tempfile`, so it can abort mid-stream instead of after the fact.


class PathTraversalError(BadRequest):
    """An archive entry, or a requested file path, resolves outside the
    project directory."""

    def __init__(self, message: str):
        super().__init__("path_traversal", message)


class BadArchiveError(BadRequest):
    """The body handed to `POST /files` is not a readable tar.gz."""

    def __init__(self, message: str):
        super().__init__("bad_archive", message)


def extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """Unpack `archive_path` into `dest_dir`, merging with what is already
    there. Files the archive contains are overwritten; everything else in
    `dest_dir` - a database's bind-mounted data directory, say - is left
    untouched. Never `rmtree` dest_dir first; deletion only ever happens
    through the explicit DELETE route.

    Not atomic: an archive rejected partway through (a bad member after 100
    good ones) leaves those 100 already written. Nothing escapes dest_dir
    either way, and a staging-and-swap scheme is more than this PoC needs.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        tar = tarfile.open(archive_path, mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as e:
        raise BadArchiveError(str(e)) from e

    with tar:
        try:
            members = tar.getmembers()
        except (tarfile.TarError, OSError, EOFError) as e:
            raise BadArchiveError(str(e)) from e

        for member in members:
            # A name like `a/../b.txt` doesn't escape dest_dir - resolve_within
            # (used by the single-file routes) already accepts it - but left
            # as-is it makes tarfile's own directory creation fail with a
            # misleading FileExistsError on ".../a/..". Normalising first
            # keeps both code paths agreeing on what's a legitimate name.
            member.name = posixpath.normpath(member.name)
            if PurePosixPath(member.name).is_absolute():
                # tarfile's "data" filter is permissive here: it strips the
                # leading "/" and keeps the now-relative entry rather than
                # refusing it. That's safe (the write still lands inside
                # dest_dir) but not what this API promises its caller, so
                # absolute names are rejected outright, before the filter
                # gets a chance to be lenient about them.
                raise PathTraversalError(
                    f"'{member.name}' is an absolute path")

        try:
            # The filter handles everything else: `..` escapes, and symlinks
            # or hardlinks whose target resolves outside dest_dir. Hand-rolled
            # checks in this area have a long history of being subtly wrong.
            tar.extractall(dest_dir, filter="data")
        except tarfile.FilterError as e:
            raise PathTraversalError(str(e)) from e
        except tarfile.TarError as e:
            raise BadArchiveError(str(e)) from e
        # A bare OSError here - disk full, permission denied while writing
        # into dest_dir - is an environment failure, not a malformed
        # archive, and is deliberately left to propagate rather than being
        # reported to the caller as "your archive is bad".


def resolve_within(root: Path, rel_path: str) -> Path:
    """Resolve `rel_path` under `root`, refusing anything that would read or
    write outside it. `root / rel_path` alone is not enough: pathlib silently
    discards `root` when `rel_path` is absolute, and `..` components need
    resolving before the containment check means anything.
    """
    if not rel_path or PurePosixPath(rel_path).is_absolute():
        raise PathTraversalError(f"'{rel_path}' is not a relative path")
    root = root.resolve()
    candidate = (root / rel_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathTraversalError(f"'{rel_path}' escapes the project directory")
    return candidate


def tree_stats(root: Path) -> dict:
    """Unreadable folders (root-owned, written by a container) are skipped
    rather than failing the whole count."""
    count = size = 0
    for dirpath, _dirs, names in os.walk(root, onerror=lambda e: None):
        for name in names:
            try:
                size += os.lstat(os.path.join(dirpath, name)).st_size
                count += 1
            except OSError:
                continue
    return {"files": count, "bytes": size}


_HIDDEN = {".eggie"}


def list_dir(root: Path, rel: str) -> list[dict]:
    """One level of `root`/`rel`: folders first, then files, both by name.
    `.eggie` is the project's own bookkeeping and never shown."""
    folder = resolve_within(root, rel) if rel else root.resolve()
    if not folder.is_dir():
        raise FileNotFoundError(rel)
    entries = []
    for entry in os.scandir(folder):
        if entry.name in _HIDDEN:
            continue
        try:
            info = entry.stat(follow_symlinks=False)
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            # Removed between the scandir above and this stat, or owned by a
            # program in the project that leaves it unreadable -- either way
            # one bad entry must not fail the whole listing.
            continue
        if is_dir:
            try:
                items = len(os.listdir(entry.path))
            except OSError:
                items = None
            entries.append({"name": entry.name, "kind": "folder", "size": None,
                            "items": items, "modified": info.st_mtime})
        else:
            entries.append({"name": entry.name, "kind": "file",
                            "size": info.st_size, "items": None,
                            "modified": info.st_mtime})
    return sorted(entries, key=lambda e: (e["kind"] != "folder", e["name"].lower()))


def list_tree(root: Path) -> list[dict]:
    """Relative paths and sizes of every file under `root`. `root` is always
    a project directory built by the caller, so nothing here re-validates it
    the way `resolve_within` validates a request-supplied path."""
    if not root.exists():
        return []
    return [
        {"path": str(path.relative_to(root)), "size": path.stat().st_size}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
