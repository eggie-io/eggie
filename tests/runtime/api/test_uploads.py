import errno
import json
import os
import threading

import pytest

from eggie_api.infra import uploads as uploads_module
from eggie_api.errors import EggieError
from eggie_api.infra.uploads import RESERVE, UploadStore

GIB = 1024 ** 3


def _store(tmp_path, free=100 * GIB, **kw):
    return UploadStore(tmp_path / "uploads", free_bytes=lambda: free, **kw)


class HalfWriter:
    """Writes half a chunk, then fails as if the disk filled up mid-write."""

    def __init__(self, f):
        self._f = f

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()

    def seek(self, *a):
        return self._f.seek(*a)

    def write(self, data):
        self._f.write(data[: len(data) // 2])
        self._f.flush()
        raise OSError(errno.ENOSPC, "No space left on device")

    def flush(self):
        self._f.flush()

    def fileno(self):
        return self._f.fileno()


def test_a_file_that_will_not_fit_is_refused_before_any_byte(tmp_path):
    store = _store(tmp_path, free=5 * GIB)
    with pytest.raises(EggieError) as e:
        store.start("blog", "data/big.zip", 5 * GIB - RESERVE + 1, "fp", False)
    assert e.value.code == "not_enough_space"
    assert e.value.extra["free_bytes"] == 5 * GIB
    assert not (tmp_path / "uploads").exists() or not os.listdir(tmp_path / "uploads")


def test_chunks_append_and_the_offset_is_the_bytes_held(tmp_path):
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 6, "fp", False)
    assert store.append(up.id, 0, b"abc").offset == 3
    assert store.append(up.id, 3, b"def").offset == 6


def test_a_wrong_offset_is_refused_with_the_real_one(tmp_path):
    # A client that resent a chunk after a dropped reply would otherwise
    # write it twice.
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 6, "fp", False)
    store.append(up.id, 0, b"abc")
    with pytest.raises(EggieError) as e:
        store.append(up.id, 0, b"abc")
    assert (e.value.code, e.value.extra["offset"]) == ("offset_mismatch", 3)


def test_more_bytes_than_declared_are_refused(tmp_path):
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 2, "fp", False)
    with pytest.raises(EggieError) as e:
        store.append(up.id, 0, b"abc")
    assert e.value.code == "too_much_data"


def test_a_disk_full_mid_chunk_rolls_back_to_the_chunk_start(tmp_path):
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 10, "fp", False)
    store.append(up.id, 0, b"abcd")
    failing = _store(tmp_path, opener=lambda p, m: HalfWriter(open(p, m)))
    with pytest.raises(EggieError) as e:
        failing.append(up.id, 4, b"efgh")
    assert (e.value.code, e.value.extra["offset"]) == ("disk_full", 4)
    assert store.get(up.id).offset == 4


def test_a_truncate_failure_after_disk_full_reports_the_bytes_actually_on_disk(
    tmp_path, monkeypatch
):
    # The rollback truncate can itself fail (e.g. a second ENOSPC). The
    # partial write already landed, so the real offset is the file's actual
    # size, not the pre-write offset the caller asked to resume from.
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 10, "fp", False)
    store.append(up.id, 0, b"abcd")
    failing = _store(tmp_path, opener=lambda p, m: HalfWriter(open(p, m)))

    def bad_truncate(*a, **kw):
        raise OSError(errno.EIO, "cannot truncate")

    monkeypatch.setattr(uploads_module.os, "truncate", bad_truncate)

    with pytest.raises(EggieError) as e:
        failing.append(up.id, 4, b"efgh")
    assert e.value.code == "disk_full"
    assert e.value.extra["offset"] == store.get(up.id).offset


def test_finish_moves_the_file_into_place_and_forgets_the_upload(tmp_path):
    store = _store(tmp_path)
    up = store.start("blog", "data/a.bin", 3, "fp", False)
    store.append(up.id, 0, b"abc")
    target = tmp_path / "projects" / "blog" / "data" / "a.bin"
    store.finish(up.id, target)
    assert target.read_bytes() == b"abc"
    assert store.list_for("blog") == []


def test_an_unfinished_upload_cannot_be_finished(tmp_path):
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 3, "fp", False)
    with pytest.raises(EggieError) as e:
        store.finish(up.id, tmp_path / "a.bin")
    assert e.value.code == "incomplete"


def test_an_upload_id_cannot_name_a_path(tmp_path):
    with pytest.raises(EggieError) as e:
        _store(tmp_path).get("../../etc")
    assert e.value.code == "upload_not_found"


def test_get_treats_unreadable_metadata_as_no_such_upload(tmp_path):
    store = _store(tmp_path)
    up = store.start("blog", "a.bin", 3, "fp", False)
    meta_path = tmp_path / "uploads" / up.id / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["surprise_field"] = "unexpected"
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(EggieError) as e:
        store.get(up.id)
    assert e.value.code == "upload_not_found"


def test_two_concurrent_appends_at_the_same_offset_only_one_wins(tmp_path):
    # The first append to open the data file is made to block partway
    # through, still holding the store's per-upload lock, until the second
    # append has been launched -- proving the offset check and the write
    # happen atomically together, not as two separately-lockable steps.
    started_writing = threading.Event()
    let_it_finish = threading.Event()
    call_count = {"n": 0}
    call_count_lock = threading.Lock()

    class BlockingWriter:
        def __init__(self, f):
            self._f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()

        def seek(self, *a):
            return self._f.seek(*a)

        def write(self, data):
            started_writing.set()
            assert let_it_finish.wait(timeout=5), "second append never launched"
            return self._f.write(data)

        def flush(self):
            self._f.flush()

        def fileno(self):
            return self._f.fileno()

    def opener(path, mode):
        with call_count_lock:
            call_count["n"] += 1
            first_call = call_count["n"] == 1
        f = open(path, mode)
        return BlockingWriter(f) if first_call else f

    store = UploadStore(tmp_path / "uploads", free_bytes=lambda: 100 * GIB,
                        opener=opener)
    up = store.start("blog", "a.bin", 6, "fp", False)
    results = {}

    def append_a():
        try:
            results["a"] = ("ok", store.append(up.id, 0, b"abc").offset)
        except EggieError as e:
            results["a"] = ("err", e.code, e.extra.get("offset"))

    def append_b():
        try:
            results["b"] = ("ok", store.append(up.id, 0, b"xyz").offset)
        except EggieError as e:
            results["b"] = ("err", e.code, e.extra.get("offset"))

    t_a = threading.Thread(target=append_a)
    t_a.start()
    assert started_writing.wait(timeout=5), "first append never reached the write"
    t_b = threading.Thread(target=append_b)
    t_b.start()
    let_it_finish.set()
    t_a.join(timeout=5)
    t_b.join(timeout=5)

    assert results["a"] == ("ok", 3)
    assert results["b"] == ("err", "offset_mismatch", 3)
    assert (tmp_path / "uploads" / up.id / "data").read_bytes()[:3] == b"abc"


def test_uploads_untouched_for_a_week_are_swept(tmp_path):
    now = [1_000_000.0]
    store = _store(tmp_path, clock=lambda: now[0])
    old = store.start("blog", "a.bin", 3, "fp", False)
    os.utime(tmp_path / "uploads" / old.id / "data", (now[0], now[0]))
    now[0] += 7 * 24 * 3600 + 1
    fresh = store.start("blog", "b.bin", 3, "fp", False)
    os.utime(tmp_path / "uploads" / fresh.id / "data", (now[0], now[0]))
    store.sweep()
    assert [u.id for u in store.list_for("blog")] == [fresh.id]
