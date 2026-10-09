import errno
import io
import os
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eggie_api.rest.app import create_app
from eggie_api.infra import files
from eggie_api.config import ApiConfig
from eggie_api.infra.runner import Completed


def _tar_bytes(*, add_default_file=True, entries=None) -> bytes:
    """Build a tar.gz in memory. `entries` is a list of (TarInfo, data|None)
    pairs for tests that need to hand-craft a malicious member; normal
    callers just get a plain `hello.txt`."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if add_default_file:
            data = b"hello\n"
            info = tarfile.TarInfo("hello.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info, data in entries or []:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _entry(name, **kw):
    info = tarfile.TarInfo(name)
    for k, v in kw.items():
        setattr(info, k, v)
    return info


# ---------------------------------------------------------------------------
# Core-level: eggie_api.infra.files.extract_archive
# ---------------------------------------------------------------------------

def test_relative_traversal_entry_is_rejected(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    archive.write_bytes(_tar_bytes(add_default_file=False, entries=[
        (_entry("../../etc/passwd", size=4), b"pwn\n"),
    ]))
    dest = tmp_path / "project"
    with pytest.raises(files.PathTraversalError):
        files.extract_archive(archive, dest)


def test_absolute_path_entry_is_rejected(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    archive.write_bytes(_tar_bytes(add_default_file=False, entries=[
        (_entry("/etc/passwd", size=4), b"pwn\n"),
    ]))
    dest = tmp_path / "project"
    with pytest.raises(files.PathTraversalError):
        files.extract_archive(archive, dest)


def test_symlink_target_escaping_the_project_is_rejected(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    link = _entry("escape", type=tarfile.SYMTYPE, linkname="../../etc/passwd")
    archive.write_bytes(_tar_bytes(add_default_file=False, entries=[(link, None)]))
    dest = tmp_path / "project"
    with pytest.raises(files.PathTraversalError):
        files.extract_archive(archive, dest)


def test_hardlink_target_escaping_the_project_is_rejected(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    link = _entry("escape", type=tarfile.LNKTYPE, linkname="../../etc/passwd")
    archive.write_bytes(_tar_bytes(add_default_file=False, entries=[(link, None)]))
    dest = tmp_path / "project"
    with pytest.raises(files.PathTraversalError):
        files.extract_archive(archive, dest)


def test_a_body_that_is_not_an_archive_is_a_bad_archive(tmp_path):
    archive = tmp_path / "not-a-tar.tar.gz"
    archive.write_bytes(b"this is plainly not a tarball")
    dest = tmp_path / "project"
    with pytest.raises(files.BadArchiveError):
        files.extract_archive(archive, dest)


def test_an_uncompressed_tar_is_accepted(tmp_path):
    # The console sends a plain tar built from file references: gzipping a
    # large folder in the page is what ran it out of memory.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("src/app.php")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"<?ph"))
    archive = tmp_path / "plain.tar"
    archive.write_bytes(buf.getvalue())
    dest = tmp_path / "project"
    files.extract_archive(archive, dest)
    assert (dest / "src" / "app.php").read_bytes() == b"<?ph"


def test_a_non_escaping_dotdot_entry_is_accepted(tmp_path):
    # `a/../b.txt` never leaves dest_dir - resolve_within (the PUT route's
    # own check) already accepts this shape - but the raw name trips
    # tarfile's own directory creation with a misleading FileExistsError
    # unless it's normalised first. Over-rejection here is a real defect,
    # not a safe default.
    archive = tmp_path / "harmless.tar.gz"
    archive.write_bytes(_tar_bytes(add_default_file=False, entries=[
        (_entry("a/../b.txt", size=3), b"hi\n"),
    ]))
    dest = tmp_path / "project"
    files.extract_archive(archive, dest)
    assert (dest / "b.txt").read_bytes() == b"hi\n"
    assert not (dest / "a").exists()


def test_an_environment_failure_during_extraction_is_not_reported_as_a_bad_archive(
        tmp_path, monkeypatch):
    # A disk-full or permission failure while writing into dest_dir is not
    # the caller's fault and must not be reported as "your archive is bad".
    archive = tmp_path / "fine.tar.gz"
    archive.write_bytes(_tar_bytes())
    dest = tmp_path / "project"

    def _boom(self, *a, **kw):
        raise OSError("No space left on device")

    monkeypatch.setattr(tarfile.TarFile, "extractall", _boom)
    with pytest.raises(OSError, match="No space left"):
        files.extract_archive(archive, dest)


def test_extraction_merges_and_leaves_untracked_files_alone(tmp_path):
    dest = tmp_path / "project"
    dest.mkdir()
    (dest / "data").mkdir()
    (dest / "data" / "postgres.db").write_text("irreplaceable")
    (dest / "docker-compose.yml").write_text("old")

    archive = tmp_path / "update.tar.gz"
    archive.write_bytes(_tar_bytes(add_default_file=False, entries=[
        (_entry("docker-compose.yml", size=3), b"new"),
    ]))
    files.extract_archive(archive, dest)

    assert (dest / "docker-compose.yml").read_text() == "new"
    assert (dest / "data" / "postgres.db").read_text() == "irreplaceable"


def test_resolve_within_rejects_absolute_and_traversal_paths(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(files.PathTraversalError):
        files.resolve_within(root, "/etc/passwd")
    with pytest.raises(files.PathTraversalError):
        files.resolve_within(root, "../../etc/passwd")


def test_resolve_within_allows_a_nested_relative_path(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    resolved = files.resolve_within(root, ".eggie/project.yml")
    assert resolved == (root / ".eggie" / "project.yml").resolve()


def test_list_dir_skips_an_entry_whose_stat_raises(tmp_path, monkeypatch):
    # A file removed between scandir and stat -- or one a container in the
    # project owns and this process can't read -- must not fail the whole
    # listing, the way test_reconcile.py's discover() already tolerates it.
    root = tmp_path / "project"
    root.mkdir()
    (root / "ok.txt").write_text("hi")
    (root / "bad.txt").write_text("nope")

    real_stat = os.DirEntry.stat

    def flaky_stat(entry, *a, **kw):
        if entry.name == "bad.txt":
            raise OSError("permission denied")
        return real_stat(entry, *a, **kw)

    monkeypatch.setattr(os.DirEntry, "stat", flaky_stat)
    entries = files.list_dir(root, "")
    assert [e["name"] for e in entries] == ["ok.txt"]


# ---------------------------------------------------------------------------
# Route-level
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    token_path = tmp_path / "api.token"
    token_path.write_text("test-token")
    config = ApiConfig(projects_root=tmp_path / "projects",
                         state_db=tmp_path / "state.db", token_path=token_path)
    app = create_app(config=config)
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        yield client, config


def _create(client, pid="blog"):
    resp = client.post("/projects", json={"id": pid})
    assert resp.status_code == 201, resp.text
    return resp


def _stray_temp_files(config) -> list[str]:
    """Anything besides project directories left directly under
    projects_root - an orphaned `.upload` staging file, most likely."""
    return [p.name for p in config.projects_root.iterdir() if p.suffix == ".upload"]


def test_upload_route_rejects_a_traversal_archive(env):
    client, config = env
    _create(client)
    archive = _tar_bytes(add_default_file=False, entries=[
        (_entry("../../etc/passwd", size=4), b"pwn\n"),
    ])
    resp = client.post("/projects/blog/files", content=archive,
                       headers={"Content-Type": "application/gzip"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "path_traversal"
    assert _stray_temp_files(config) == []


def test_upload_route_rejects_a_body_that_is_not_an_archive(env):
    client, config = env
    _create(client)
    resp = client.post("/projects/blog/files", content=b"not a tarball",
                       headers={"Content-Type": "application/gzip"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_archive"
    assert _stray_temp_files(config) == []


def test_a_client_disconnect_mid_upload_does_not_leak_a_temp_file(env):
    # There is no size cap on this route by design, so an unremoved partial
    # upload is an unbounded way to fill the VM's disk.
    client, config = env
    _create(client)

    def dropped_connection():
        yield b"x" * 1024
        raise RuntimeError("client vanished")

    raw = TestClient(client.app, raise_server_exceptions=False,
                     headers={"Authorization": "Bearer test-token"})
    resp = raw.post("/projects/blog/files", content=dropped_connection())
    assert resp.status_code == 500
    assert _stray_temp_files(config) == []


def test_upload_to_an_unknown_project_is_project_not_found(env):
    client, _config = env
    resp = client.post("/projects/nope/files", content=_tar_bytes())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"


def test_archive_upload_disk_full_is_507_and_leaves_no_temp_file(env, monkeypatch):
    client, config = env
    _create(client)

    def _disk_full_on_extract(*a, **kw):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(files, "extract_archive", _disk_full_on_extract)
    resp = client.post("/projects/blog/files", content=_tar_bytes())
    assert resp.status_code == 507
    assert resp.json()["error"]["code"] == "disk_full"
    assert _stray_temp_files(config) == []


def test_upload_merges_and_tree_lists_tracked_and_untracked_files(env):
    client, config = env
    _create(client)

    first = _tar_bytes(add_default_file=False, entries=[
        (_entry("docker-compose.yml", size=8), b"services"),
    ])
    assert client.post("/projects/blog/files", content=first).status_code == 200

    # A file the upload never touches - a bind-mounted data directory, say.
    (config.projects_root / "blog" / "data").mkdir()
    (config.projects_root / "blog" / "data" / "state.db").write_text("keep-me")

    second = _tar_bytes(add_default_file=False, entries=[
        (_entry("docker-compose.yml", size=11), b"new-compose"),
    ])
    assert client.post("/projects/blog/files", content=second).status_code == 200

    tree = {f["path"]: f["size"]
           for f in client.get("/projects/blog/files").json()["files"]}
    assert tree["docker-compose.yml"] == 11
    assert tree["data/state.db"] == len("keep-me")
    assert (config.projects_root / "blog" / "data" / "state.db").read_text() == "keep-me"


def test_put_get_delete_single_file_round_trip(env):
    client, _config = env
    _create(client)

    put = client.put("/projects/blog/files/.eggie/project.yml", content=b"id: blog\n")
    assert put.status_code == 200, put.text

    got = client.get("/projects/blog/files/.eggie/project.yml")
    assert got.status_code == 200
    assert got.content == b"id: blog\n"

    assert client.delete("/projects/blog/files/.eggie/project.yml").status_code == 200
    assert client.get("/projects/blog/files/.eggie/project.yml").status_code == 404


def test_get_missing_file_is_file_not_found(env):
    client, _config = env
    _create(client)
    resp = client.get("/projects/blog/files/nope.txt")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "file_not_found"


def test_get_file_route_rejects_traversal(env):
    client, _config = env
    _create(client)
    # httpx collapses a literal ".." in the URL before it ever leaves the
    # client, so this uses the percent-encoded form to prove the *server*
    # rejects it too, not just well-behaved HTTP clients.
    resp = client.get("/projects/blog/files/%2e%2e/%2e%2e/etc/passwd")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "path_traversal"


def test_a_5mb_archive_round_trips_proving_the_command_line_ceiling_is_gone(env):
    client, _config = env
    _create(client)

    payload = (b"eggie-poc-" * 550_000)  # ~5.5 MB, well past the old ~24 KB cap
    assert len(payload) > 5 * 1024 * 1024
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("big.bin")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    resp = client.post("/projects/blog/files", content=buf.getvalue())
    assert resp.status_code == 200, resp.text

    got = client.get("/projects/blog/files/big.bin")
    assert got.status_code == 200
    assert got.content == payload


# ---------------------------------------------------------------------------
# Upload size cap
# ---------------------------------------------------------------------------

@pytest.fixture
def capped_env(tmp_path):
    """A small cap so the tests below don't need a multi-hundred-MB body to
    exercise it."""
    token_path = tmp_path / "api.token"
    token_path.write_text("test-token")
    config = ApiConfig(projects_root=tmp_path / "projects",
                         state_db=tmp_path / "state.db", token_path=token_path,
                         max_upload_bytes=1024)
    app = create_app(config=config)
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        yield client, config


def test_archive_upload_over_the_cap_is_413_and_leaves_no_temp_file(capped_env):
    client, config = capped_env
    _create(client)
    resp = client.post("/projects/blog/files", content=b"x" * 2000)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"
    assert _stray_temp_files(config) == []


def test_single_file_put_over_the_cap_is_413_and_leaves_no_temp_file(capped_env):
    client, config = capped_env
    _create(client)
    resp = client.put("/projects/blog/files/big.bin", content=b"x" * 2000)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"
    assert _stray_temp_files(config) == []
    assert not (config.projects_root / "blog" / "big.bin").exists()


def test_the_upload_cap_is_reported_in_a_size_a_person_can_read(tmp_path):
    # "upload exceeds the 536870912 byte limit" is the raw constant read out
    # loud; the people this tool is for do not count bytes.
    token_path = tmp_path / "api.token"
    token_path.write_text("test-token")
    config = ApiConfig(projects_root=tmp_path / "projects",
                         state_db=tmp_path / "state.db", token_path=token_path,
                         max_upload_bytes=1024 * 1024)
    app = create_app(config=config)
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        _create(client)
        message = client.post("/projects/blog/files",
                              content=b"x" * (2 * 1024 * 1024)
                              ).json()["error"]["message"]

    assert "1 MB" in message
    assert str(config.max_upload_bytes) not in message


def test_browsing_a_permission_denied_folder_is_409_not_a_500(env):
    if os.geteuid() == 0:
        pytest.skip("root ignores a folder's permission bits")
    client, config = env
    _create(client)
    locked = config.projects_root / "blog" / "locked"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        resp = client.get("/projects/blog/files", params={"dir": "locked"})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "permission_denied"
    finally:
        locked.chmod(0o755)


def test_remove_tree_falls_back_to_root_for_files_a_container_owns(tmp_path):
    from eggie_api.infra import docker as lifecycle

    class Runner:
        def __init__(self):
            self.calls = []

        def exec(self, argv, *, root=False):
            self.calls.append(argv)
            if argv[:2] == [lifecycle.DOCKER, "inspect"]:
                return Completed(0, "ghcr.io/x/eggie-api:9\n", "")
            return Completed(0, "", "")

    runner = Runner()
    lifecycle.remove_tree_as_root(runner, tmp_path / "projects" / "blog")
    run = runner.calls[-1]
    assert run[:3] == [lifecycle.DOCKER, "run", "--rm"]
    assert ["--user", "0"] == run[run.index("--user"):run.index("--user") + 2]
    assert f"{tmp_path / 'projects'}:{tmp_path / 'projects'}" in run
    assert run[-3:] == ["rm", "-rf", str(tmp_path / "projects" / "blog")]
