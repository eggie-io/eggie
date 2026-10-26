from __future__ import annotations

import io
import json
import tarfile

import pytest

from eggie_api.domain.push import build_manifest
from eggie_api.infra.bundle import (
    main, pack_argv, restore_volume, unpack, unpack_argv, write_bundle)
from eggie_api.infra.files import BadArchiveError, PathTraversalError


def _project(root):
    for rel, text in {
        "src/app.py": "print()", "node_modules/x": "x", ".git/config": "c",
        ".eggie/overlay.yml": "o", ".eggie/project.yml": "p", ".env": "A=1",
        "pgdata/base/1": "row",
    }.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return root


def _names(out):
    with tarfile.open(out) as tar:
        return set(tar.getnames())


def test_bundle_has_manifest_files_and_no_excluded_paths(tmp_path):
    project = _project(tmp_path / "p")
    out = tmp_path / "b.tar"
    write_bundle(out, project, {"format": 1}, volumes={})
    names = _names(out)
    assert {"manifest.json", "files/src/app.py", "files/.eggie/project.yml",
            "files/.env"} <= names
    assert not any("node_modules" in n or ".git/" in n or "overlay.yml" in n
                   for n in names)


def test_symlinks_are_stored_as_links_not_followed(tmp_path):
    project = _project(tmp_path / "p")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.txt").write_text("TOPSECRET")
    (project / "out").symlink_to(elsewhere / "secret.txt")
    (project / "dirlink").symlink_to(elsewhere)
    out = tmp_path / "b.tar"
    write_bundle(out, project, {}, volumes={})
    with tarfile.open(out) as tar:
        assert tar.getmember("files/out").issym()
        assert tar.getmember("files/dirlink").issym()
        assert not any("secret.txt" in n for n in tar.getnames())
        assert all(b"TOPSECRET" not in (tar.extractfile(m).read() if m.isfile() else b"")
                   for m in tar.getmembers())


def test_restore_volume_replaces_contents(tmp_path):
    source, target = tmp_path / "s", tmp_path / "t"
    (source / "d").mkdir(parents=True)
    (source / "new.txt").write_text("n")
    (source / "d" / "link").symlink_to("../new.txt")
    target.mkdir()
    (target / "old.txt").write_text("o")
    (target / "olddir").mkdir()
    restore_volume(source, target)
    assert sorted(p.name for p in target.iterdir()) == ["d", "new.txt"]
    assert (target / "d" / "link").is_symlink()


def test_pack_argv_mounts_project_read_only_and_runs_the_module():
    argv = pack_argv("img", out_dir="/opt/eggie/transfers/out",
                     project_dir="/opt/eggie/projects/shop",
                     volumes={"dbv": "shop_dbv"}, out_name="j1.tar",
                     manifest_path="/out/j1.json")
    assert "/opt/eggie/projects/shop:/src:ro" in argv and "shop_dbv:/vol/dbv:ro" in argv
    assert argv[argv.index("img") + 1:argv.index("img") + 4] == ["-m", "eggie_api.infra.bundle", "pack"]


def test_cli_pack_sends_every_file_including_bind_folders_and_the_volumes(tmp_path):
    project = _project(tmp_path / "p")
    vol = tmp_path / "vol"
    vol.mkdir()
    (vol / "f").write_text("v")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(build_manifest(volumes={"dbv": "shop_dbv"}, secrets={})))
    out = tmp_path / "b.tar"
    assert main(["pack", "--out", str(out), "--project", str(project),
                 "--manifest", str(manifest), "--volume", f"dbv={vol}"]) == 0
    assert {"files/src/app.py", "files/pgdata/base/1", "volumes/dbv/f"} <= _names(out)


def test_restore_volume_with_missing_source_keeps_the_target(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    (target / "keep.txt").write_text("k")
    with pytest.raises(FileNotFoundError):
        restore_volume(tmp_path / "nope", target)
    assert (target / "keep.txt").exists()


def test_bundle_round_trips_through_unpack(tmp_path):
    project = _project(tmp_path / "p")
    vol = tmp_path / "vol"
    vol.mkdir()
    (vol / "f").write_text("v")
    out, dest = tmp_path / "b.tar", tmp_path / "dest"
    dest.mkdir()
    write_bundle(out, project, {"format": 1}, volumes={"dbv": vol})
    unpack(out, dest)
    assert (dest / "files/src/app.py").read_text() == "print()"
    assert (dest / "files/pgdata/base/1").exists()
    assert (dest / "volumes/dbv/f").read_text() == "v"
    assert json.loads((dest / "manifest.json").read_text()) == {"format": 1}


def _tar_with(path, info, payload=b""):
    with tarfile.open(path, "w") as tar:
        tar.addfile(info, io.BytesIO(payload))


@pytest.mark.parametrize("make, error", [
    (lambda: tarfile.TarInfo("/etc/passwd"), PathTraversalError),
    (lambda: tarfile.TarInfo("../escape"), PathTraversalError),
    (lambda: _typed("files/dev", tarfile.CHRTYPE), BadArchiveError),
    (lambda: _typed("files/pipe", tarfile.FIFOTYPE), BadArchiveError),
])
def test_unpack_refuses_unsafe_members(tmp_path, make, error):
    bundle, dest = tmp_path / "b.tar", tmp_path / "dest"
    dest.mkdir()
    _tar_with(bundle, make())
    with pytest.raises(error):
        unpack(bundle, dest)
    assert not (tmp_path / "escape").exists()


def _typed(name, kind):
    info = tarfile.TarInfo(name)
    info.type = kind
    return info


def test_unpack_argv_mounts_bundle_read_only_and_dest_writable():
    argv = unpack_argv("img", bundle_path="/opt/eggie/transfers/in/j.tar", dest_dir="/opt/eggie/stage")
    assert "/opt/eggie/transfers/in:/in:ro" in argv and "/opt/eggie/stage:/dest" in argv
    assert argv[argv.index("img") + 1:argv.index("img") + 4] == ["-m", "eggie_api.infra.bundle", "unpack"]


@pytest.mark.parametrize("linkname", ["/etc/passwd", "../outside/secret"])
def test_unpack_refuses_hard_links_leaving_the_bundle(tmp_path, linkname):
    bundle, dest = tmp_path / "b.tar", tmp_path / "dest"
    dest.mkdir()
    info = tarfile.TarInfo("files/link")
    info.type, info.linkname = tarfile.LNKTYPE, linkname
    _tar_with(bundle, info)
    with pytest.raises(PathTraversalError):
        unpack(bundle, dest)


def test_unpack_keeps_hard_links_inside_the_bundle(tmp_path):
    bundle, dest = tmp_path / "b.tar", tmp_path / "dest"
    dest.mkdir()
    with tarfile.open(bundle, "w") as tar:
        data = tarfile.TarInfo("files/a")
        data.size = 1
        tar.addfile(data, io.BytesIO(b"x"))
        link = tarfile.TarInfo("files/b")
        link.type, link.linkname = tarfile.LNKTYPE, "files/a"
        tar.addfile(link)
    unpack(bundle, dest)
    assert (dest / "files/b").read_text() == "x"


def test_unpack_makes_project_files_group_writable_and_keeps_volume_modes(tmp_path):
    project = _project(tmp_path / "p")
    (project / "run.sh").write_text("#!/bin/sh\n")
    (project / "run.sh").chmod(0o755)
    (project / "src" / "app.py").chmod(0o644)
    (project / "pgdata" / "base" / "1").chmod(0o600)
    (project / "pgdata").chmod(0o700)
    vol = tmp_path / "vol"
    vol.mkdir()
    (vol / "shared").write_text("v")
    (vol / "shared").chmod(0o664)
    (vol / "tool").write_text("t")
    (vol / "tool").chmod(0o4755)
    out, dest = tmp_path / "b.tar", tmp_path / "dest"
    dest.mkdir()
    write_bundle(out, project, {"format": 1}, volumes={"dbv": vol})

    unpack(out, dest)

    def mode(rel):
        return (dest / rel).stat().st_mode & 0o7777
    assert mode("files/src/app.py") == 0o664
    assert mode("files") == 0o2775
    assert mode("files/src") == 0o2775
    assert mode("files/run.sh") == 0o775
    # A bind folder is a project file like any other, whatever mounts it.
    assert mode("files/pgdata") == 0o2770
    assert mode("files/pgdata/base/1") == 0o660
    assert mode("volumes/dbv/shared") == 0o664
    assert mode("volumes/dbv/tool") == 0o755


def test_unpack_gives_project_files_the_group_of_the_folder_it_unpacks_into(tmp_path, monkeypatch):
    bundle, dest = tmp_path / "b.tar", tmp_path / "dest"
    dest.mkdir()
    with tarfile.open(bundle, "w") as tar:
        for name in ("files/app.py", "volumes/dbv/f"):
            info = tarfile.TarInfo(name)
            info.gid = 4242
            tar.addfile(info, io.BytesIO(b""))
    # Owners only change as root; record the group each entry would get.
    given = {}
    monkeypatch.setattr(tarfile.TarFile, "chown",
                        lambda self, member, path, numeric: given.update({member.name: member.gid}))

    unpack(bundle, dest)

    assert given == {"files/app.py": dest.stat().st_gid, "volumes/dbv/f": 4242}
