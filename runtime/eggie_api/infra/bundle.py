"""Packs a project into a transfer bundle and restores a volume from one.
Runs as `python -m eggie_api.infra.bundle` as root in a throwaway container of
the API image; keep it free of anything that opens sqlite or starts threads."""
from __future__ import annotations

import argparse
import grp
import io
import json
import os
import posixpath
import shutil
import sys
import tarfile
from collections.abc import Iterator, Sequence
from pathlib import Path

from ..domain.push import is_excluded
from .docker import DOCKER
from .files import BadArchiveError, PathTraversalError

PROJECT_GROUP = "docker"
MAX_MANIFEST = 1024 ** 2


def _is_data(rel: str, data_paths: Sequence[str]) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in data_paths)


def _walk(root: Path, skip) -> Iterator[tuple[Path, str]]:
    """Every entry under `root` as (path, relative name); symlinks are yielded
    but never descended into."""
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        kept = []
        for name in dirs:
            rel = (base / name).relative_to(root).as_posix()
            if skip(rel):
                continue
            kept.append(name)
            yield base / name, rel
        dirs[:] = [d for d in kept if not (base / d).is_symlink()]
        for name in files:
            rel = (base / name).relative_to(root).as_posix()
            if not skip(rel):
                yield base / name, rel


def write_bundle(out: Path, project_dir: Path, manifest: dict, *,
                 volumes: dict[str, Path], data_paths: Sequence[str],
                 include_data: bool) -> int:
    payload = json.dumps(manifest).encode("utf-8")
    with tarfile.open(out, "w") as tar:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
        tar.add(project_dir, arcname="files", recursive=False)
        for path, rel in _walk(project_dir, lambda r: is_excluded(r) or (
                not include_data and _is_data(r, data_paths))):
            tar.add(path, arcname=f"files/{rel}", recursive=False)
        if include_data:
            for key, source in volumes.items():
                tar.add(source, arcname=f"volumes/{key}", recursive=False)
                for path, rel in _walk(source, lambda _r: False):
                    tar.add(path, arcname=f"volumes/{key}/{rel}",
                            recursive=False)
    return out.stat().st_size


def restore_volume(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(str(source))
    for child in target.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
    # Ownership only transfers as root; tests run unprivileged.
    if os.geteuid() == 0:
        for path, rel in [(source, "")] + [(p, r) for p, r in _walk(source, lambda _r: False)]:
            stat = path.lstat()
            os.chown(target / rel if rel else target, stat.st_uid, stat.st_gid,
                     follow_symlinks=False)


def _manifest_data_paths(tar: tarfile.TarFile) -> tuple[str, ...]:
    """Best effort: the API vets the manifest after unpacking and refuses a bad one."""
    try:
        member = tar.getmember("manifest.json")
        if not member.isreg() or member.size > MAX_MANIFEST:
            return ()
        paths = json.loads(tar.extractfile(member).read()).get("data_paths")
    except (KeyError, ValueError, AttributeError):
        return ()
    if not isinstance(paths, list):
        return ()
    return tuple(posixpath.normpath(p) for p in paths if isinstance(p, str))


def _project_gid() -> int | None:
    try:
        return grp.getgrnam(PROJECT_GROUP).gr_gid
    except KeyError:
        return None


def _ownership_filter(data_paths: Sequence[str], gid: int | None):
    """Data (volumes and data_paths) keeps the sender's owners and modes, less the
    high bits; every other project file joins the project group, group-writable."""
    def apply(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo:
        mode = member.mode
        member = tarfile.tar_filter(member, dest)
        name = posixpath.normpath(member.name)
        rel = name.removeprefix("files/") if name.startswith("files/") else None
        if mode is None or member.mode is None:
            return member
        if name == "volumes" or name.startswith("volumes/") or (
                rel is not None and _is_data(rel, data_paths)):
            return member.replace(mode=mode & 0o777, deep=False)
        if name == "files" or rel is not None:
            execute = 0o010 if member.isdir() or member.mode & 0o111 else 0
            changes = {"mode": member.mode | 0o060 | execute}
            if gid is not None:
                changes["gid"] = gid
            return member.replace(**changes, deep=False)
        return member
    return apply


def unpack(bundle: Path, dest: Path) -> None:
    with tarfile.open(bundle) as tar:
        members = tar.getmembers()
        for m in members:
            if m.name.startswith("/"):
                raise PathTraversalError(f"absolute path in bundle: {m.name}")
            if m.ischr() or m.isblk() or m.isfifo():
                raise BadArchiveError(f"special file in bundle: {m.name}")
            # filter="tar" does not vet hard-link targets.
            if m.islnk():
                target = os.path.normpath(os.path.join(dest, m.linkname))
                if m.linkname.startswith("/") or not target.startswith(
                        os.path.join(os.path.normpath(dest), "")):
                    raise PathTraversalError(f"hard link escapes bundle: {m.name}")
        apply = _ownership_filter(_manifest_data_paths(tar), _project_gid())
        try:
            tar.extractall(dest, members=members, filter=apply, numeric_owner=True)
        except tarfile.FilterError as e:
            raise PathTraversalError(str(e)) from e


def pack_argv(image: str, *, out_dir, project_dir, volumes: dict[str, str],
              out_name: str, manifest_path: str, include_data: bool) -> list[str]:
    argv = [DOCKER, "run", "--rm", "--user", "0", "--entrypoint", "python",
            "-v", f"{project_dir}:/src:ro", "-v", f"{out_dir}:/out"]
    # Docker would silently create a missing volume, so mount only when shipping data.
    volumes = volumes if include_data else {}
    for key, name in volumes.items():
        argv += ["-v", f"{name}:/vol/{key}:ro"]
    argv += [image, "-m", "eggie_api.infra.bundle", "pack",
             "--out", f"/out/{out_name}", "--project", "/src",
             "--manifest", manifest_path]
    for key in volumes:
        argv += ["--volume", f"{key}=/vol/{key}"]
    if include_data:
        argv.append("--data")
    return argv


def restore_argv(image: str, *, source_dir, volume_name: str) -> list[str]:
    return [DOCKER, "run", "--rm", "--user", "0", "--entrypoint", "python",
            "-v", f"{source_dir}:/src:ro", "-v", f"{volume_name}:/vol",
            image, "-m", "eggie_api.infra.bundle", "restore",
            "--source", "/src", "--target", "/vol"]


def unpack_argv(image: str, *, bundle_path, dest_dir) -> list[str]:
    bundle_dir, bundle_name = os.path.split(str(bundle_path))
    return [DOCKER, "run", "--rm", "--user", "0", "--entrypoint", "python",
            "-v", f"{bundle_dir}:/in:ro", "-v", f"{dest_dir}:/dest",
            image, "-m", "eggie_api.infra.bundle", "unpack",
            "--bundle", f"/in/{bundle_name}", "--dest", "/dest"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="eggie_api.infra.bundle")
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("pack")
    pack.add_argument("--out", required=True)
    pack.add_argument("--project", required=True)
    pack.add_argument("--manifest", required=True)
    pack.add_argument("--volume", action="append", default=[])
    pack.add_argument("--data", action="store_true")
    restore = sub.add_parser("restore")
    restore.add_argument("--source", required=True)
    restore.add_argument("--target", required=True)
    unpack_cmd = sub.add_parser("unpack")
    unpack_cmd.add_argument("--bundle", required=True)
    unpack_cmd.add_argument("--dest", required=True)
    args = parser.parse_args(argv)
    if args.command == "unpack":
        unpack(Path(args.bundle), Path(args.dest))
        return 0
    if args.command == "restore":
        restore_volume(Path(args.source), Path(args.target))
        return 0
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    volumes = {k: Path(v) for k, _, v in (s.partition("=") for s in args.volume)}
    write_bundle(Path(args.out), Path(args.project), manifest, volumes=volumes,
                 data_paths=manifest.get("data_paths", []),
                 include_data=args.data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
