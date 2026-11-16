import stat

from eggie_api.infra import secret_files


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_write_all_replaces_previous_files(tmp_path):
    secret_files.write_all(tmp_path, "shop", {"A": b"one", "B": b"two"})
    folder = secret_files.write_all(tmp_path, "shop", {"A": b"uno"})
    assert sorted(p.name for p in folder.iterdir()) == ["A"]
    assert (folder / "A").read_bytes() == b"uno"
    assert _mode(folder) == 0o700
    assert _mode(folder / "A") == 0o444


def test_write_all_without_files_removes_folder_contents(tmp_path):
    secret_files.write_all(tmp_path, "shop", {"A": b"one"})
    folder = secret_files.write_all(tmp_path, "shop", {})
    assert list(folder.iterdir()) == []
