import threading

import pytest

from eggie_api.infra.uploads import RESERVE
from tests.runtime.api.conftest import _create


@pytest.fixture
def blog(env):
    _create(env, "blog")
    return env


def _start(env, path="data/a.bin", size=6, **extra):
    return env.client.post("/projects/blog/uploads", json={
        "path": path, "size": size, "fingerprint": "a.bin:6:1", **extra})


def _patch(env, uid, offset, data):
    return env.client.patch(f"/uploads/{uid}", content=data,
                            headers={"Upload-Offset": str(offset)})


def test_a_chunked_upload_lands_in_the_chosen_folder(blog):
    uid = _start(blog).json()["upload_id"]
    _patch(blog, uid, 0, b"abc")
    done = _patch(blog, uid, 3, b"def").json()
    assert done["done"] is True
    assert (blog.config.projects_root / "blog" / "data" / "a.bin").read_bytes() == b"abcdef"


def test_a_partial_upload_is_invisible_to_the_file_listing(blog):
    # The coding agent reads the project folder; a half file there is a
    # corrupt dump it will happily try to import.
    uid = _start(blog).json()["upload_id"]
    _patch(blog, uid, 0, b"abc")
    assert blog.client.get("/projects/blog/files").json()["files"] == []


def test_a_reopened_page_can_find_the_unfinished_upload(blog):
    uid = _start(blog).json()["upload_id"]
    _patch(blog, uid, 0, b"abc")
    pending = blog.client.get("/projects/blog/uploads").json()["uploads"]
    assert [(u["id"], u["offset"], u["fingerprint"]) for u in pending] == [
        (uid, 3, "a.bin:6:1")]


def test_an_upload_path_cannot_escape_the_project(blog):
    resp = _start(blog, path="../other/x")
    assert resp.json()["error"]["code"] == "path_traversal"


def test_an_existing_file_is_not_replaced_unless_asked(blog):
    (blog.config.projects_root / "blog").mkdir(parents=True, exist_ok=True)
    (blog.config.projects_root / "blog" / "a.bin").write_bytes(b"old")
    assert _start(blog, path="a.bin").json()["error"]["code"] == "file_exists"
    assert _start(blog, path="a.bin", replace=True).status_code == 201


def test_uploading_onto_an_existing_folder_is_refused_even_with_replace(blog):
    (blog.config.projects_root / "blog" / "data").mkdir(parents=True)
    resp = _start(blog, path="data", replace=True)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "path_is_folder"


def test_the_offset_mismatch_body_carries_the_real_offset(blog):
    uid = _start(blog).json()["upload_id"]
    _patch(blog, uid, 0, b"abc")
    err = _patch(blog, uid, 0, b"abc").json()["error"]
    assert (err["code"], err["offset"]) == ("offset_mismatch", 3)


def test_a_file_bigger_than_free_space_is_refused_up_front(blog):
    free = blog.client.get("/disk").json()["free_bytes"]
    resp = _start(blog, size=free - RESERVE + 1)
    assert resp.status_code == 507
    assert resp.json()["error"]["code"] == "not_enough_space"


def test_one_folder_level_is_listed_with_counts(blog):
    root = blog.config.projects_root / "blog"
    (root / "data").mkdir(parents=True)
    (root / "data" / "x.csv").write_bytes(b"12345")
    (root / ".eggie").mkdir()
    (root / "README.md").write_text("hi")
    entries = blog.client.get("/projects/blog/files", params={"dir": ""}).json()["entries"]
    assert [(e["name"], e["kind"], e["items"], e["size"]) for e in entries] == [
        ("data", "folder", 1, None), ("README.md", "file", None, 2)]


def test_finishing_an_upload_for_a_deleted_project_is_refused(blog):
    uid = _start(blog).json()["upload_id"]
    _patch(blog, uid, 0, b"abc")
    assert blog.client.delete("/projects/blog").status_code == 200
    resp = _patch(blog, uid, 3, b"def")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"
    assert not (blog.config.projects_root / "blog" / "data" / "a.bin").exists()
    assert blog.client.get(f"/uploads/{uid}").status_code == 404


def test_finish_upload_holds_the_project_lock_across_the_existence_check(blog):
    # Checking "does the project still exist" before taking the lock leaves a
    # window where a concurrent delete finishes in the gap; the check has to
    # happen only once the lock is already held, so a delete attempted then
    # is refused as busy rather than racing ahead of it.
    uid = _start(blog).json()["upload_id"]
    checking = threading.Event()
    release = threading.Event()
    real_get_project = blog.repos.projects.get
    calls = {"n": 0}

    def blocking_get_project(project_id):
        result = real_get_project(project_id)
        calls["n"] += 1
        if calls["n"] == 1:
            # Only finish_upload's own check blocks -- the DELETE request's
            # unrelated require_row() lookup, made below, must go straight
            # through so it can test the lock, not deadlock behind it.
            checking.set()
            assert release.wait(5), "finish_upload's check never resumed"
        return result

    blog.repos.projects.get = blocking_get_project
    outcome = {}

    def do_finish():
        outcome["resp"] = _patch(blog, uid, 0, b"abcdef")

    t = threading.Thread(target=do_finish)
    t.start()
    try:
        assert checking.wait(5), "finish_upload never reached its existence check"
        delete_resp = blog.client.delete("/projects/blog")
    finally:
        release.set()
        t.join(5)

    assert delete_resp.status_code == 409
    assert delete_resp.json()["error"]["code"] == "project_busy"
    assert outcome["resp"].json()["done"] is True


def test_a_download_is_offered_as_an_attachment(blog):
    root = blog.config.projects_root / "blog"
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("hi")
    resp = blog.client.get("/projects/blog/files/notes.txt")
    assert resp.headers["content-disposition"].startswith("attachment")
