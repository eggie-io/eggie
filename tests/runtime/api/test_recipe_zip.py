import pytest

from eggie_api.infra.recipe_zip import BadBundle, unpack
from tests.runtime.api.recipe_zips import zip_bytes

CAPS = {"max_files": 100, "max_unpacked_bytes": 1_000_000}


def _unpack(tmp_path, entries, **caps):
    source = tmp_path / "bundle.zip"
    source.write_bytes(zip_bytes(entries))
    dest = tmp_path / "out"
    unpack(source, dest, **{**CAPS, **caps})
    return dest


def _refused(tmp_path, entries, **caps) -> BadBundle:
    with pytest.raises(BadBundle) as raised:
        _unpack(tmp_path, entries, **caps)
    assert not (tmp_path / "out").exists()
    return raised.value


def _recipe(*extra):
    return [("RECIPE.md", b"# r\n", 0o100644), *extra]


def test_unpacks_files_and_keeps_only_the_executable_bit(tmp_path):
    dest = _unpack(tmp_path, _recipe(("scripts/run.sh", b"#!/bin/sh\n", 0o100755),
                                     ("x.txt", b"x", 0o104777)))
    assert (dest / "RECIPE.md").stat().st_mode & 0o7777 == 0o644
    assert (dest / "scripts/run.sh").stat().st_mode & 0o7777 == 0o755
    assert (dest / "x.txt").stat().st_mode & 0o7777 == 0o755
    assert (dest / "scripts").stat().st_mode & 0o7777 == 0o755
    assert (dest / "scripts/run.sh").read_bytes() == b"#!/bin/sh\n"


def test_an_entry_without_unix_mode_is_a_plain_file(tmp_path):
    dest = _unpack(tmp_path, _recipe(("notes.txt", b"n", 0)))
    assert (dest / "notes.txt").stat().st_mode & 0o7777 == 0o644


@pytest.mark.parametrize("name", ["../evil", "/etc/evil", "a/../../evil", "a\\evil"])
def test_a_path_leaving_the_folder_is_refused(tmp_path, name):
    _refused(tmp_path, _recipe((name, b"x", 0o100644)))
    assert not (tmp_path / "evil").exists()


def test_a_symlink_entry_is_refused(tmp_path):
    _refused(tmp_path, _recipe(("link", b"/etc/passwd", 0o120777)))


def test_too_many_entries_are_refused(tmp_path):
    _refused(tmp_path, _recipe(*[(f"f{i}", b"x", 0o100644) for i in range(3)]),
             max_files=3)


def test_a_declared_size_over_the_cap_is_refused_before_writing(tmp_path):
    _refused(tmp_path, _recipe(("a", b"x" * 600, 0o100644), ("b", b"x" * 600, 0o100644)),
             max_unpacked_bytes=1000)


def test_recipe_md_must_be_at_the_root(tmp_path):
    error = _refused(tmp_path, [("docs/RECIPE.md", b"# r\n", 0o100644)])
    assert "RECIPE.md" in str(error)


def test_a_file_that_is_not_a_zip_is_refused(tmp_path):
    source = tmp_path / "bundle.zip"
    source.write_bytes(b"<html>")
    with pytest.raises(BadBundle):
        unpack(source, tmp_path / "out", **CAPS)
    assert not (tmp_path / "out").exists()
