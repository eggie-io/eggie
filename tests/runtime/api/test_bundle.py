from __future__ import annotations

import json
import tarfile

from eggie_api.infra.bundle import pack_argv, restore_volume, write_bundle


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
    write_bundle(out, project, {"format": 1}, volumes={}, data_paths=[],
                 include_data=False)
    names = _names(out)
    assert {"manifest.json", "files/src/app.py", "files/.eggie/project.yml",
            "files/.env"} <= names
    assert not any("node_modules" in n or ".git/" in n or "overlay.yml" in n
                   for n in names)


def test_data_paths_only_with_include_data(tmp_path):
    project = _project(tmp_path / "p")
    vol = tmp_path / "vol"
    vol.mkdir()
    (vol / "f").write_text("v")
    kwargs = dict(volumes={"dbv": vol}, data_paths=["pgdata"])
    without, with_ = tmp_path / "a.tar", tmp_path / "b.tar"
    write_bundle(without, project, {}, include_data=False, **kwargs)
    write_bundle(with_, project, {}, include_data=True, **kwargs)
    assert not any("pgdata" in n or n.startswith("volumes/") for n in _names(without))
    assert {"files/pgdata/base/1", "volumes/dbv/f"} <= _names(with_)


def test_symlinks_are_stored_as_links_not_followed(tmp_path):
    project = _project(tmp_path / "p")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.txt").write_text("TOPSECRET")
    (project / "out").symlink_to(elsewhere / "secret.txt")
    (project / "dirlink").symlink_to(elsewhere)
    out = tmp_path / "b.tar"
    write_bundle(out, project, {}, volumes={}, data_paths=[], include_data=False)
    with tarfile.open(out) as tar:
        assert tar.getmember("files/out").issym()
        assert tar.getmember("files/dirlink").issym()
        assert not any("secret.txt" in n for n in tar.getnames())
        assert all(b"TOPSECRET" not in (tar.extractfile(m).read() if m.isfile() else b"")
                   for m in tar.getmembers())


def test_manifest_secrets_carry_only_name_and_hint(tmp_path):
    project = _project(tmp_path / "p")
    manifest = {"secrets": [{"name": "KEY", "hint": "set it"}]}
    out = tmp_path / "b.tar"
    write_bundle(out, project, manifest, volumes={}, data_paths=[], include_data=False)
    with tarfile.open(out) as tar:
        read = json.load(tar.extractfile("manifest.json"))
    assert read["secrets"] and all(set(s) == {"name", "hint"} for s in read["secrets"])


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
                     manifest_path="/out/j1.json", include_data=True)
    assert "/opt/eggie/projects/shop:/src:ro" in argv and "shop_dbv:/vol/dbv:ro" in argv
    assert argv[argv.index("img") + 1:argv.index("img") + 4] == ["-m", "eggie_api.infra.bundle", "pack"]
