import errno
import os
from types import SimpleNamespace

from eggie_api.core import disk


def _fake_statvfs(_path):
    return SimpleNamespace(f_frsize=4096, f_blocks=1000, f_bfree=300,
                           f_bavail=200)


def test_free_space_excludes_blocks_reserved_for_root(tmp_path):
    # f_bfree counts the root reserve; the API runs as uid 1000 and cannot
    # write into it, so reporting it would let an upload start that must fail.
    assert disk.usage(tmp_path, statvfs=_fake_statvfs) == {
        "free_bytes": 200 * 4096, "total_bytes": 1000 * 4096}


def test_usage_of_a_folder_not_created_yet_reads_its_parent(tmp_path):
    seen = []
    disk.usage(tmp_path / "projects", statvfs=lambda p: seen.append(p) or
               _fake_statvfs(p))
    assert seen == [str(tmp_path)]


def test_only_enospc_counts_as_disk_full():
    assert disk.is_disk_full(OSError(errno.ENOSPC, "full"))
    assert not disk.is_disk_full(OSError(errno.EACCES, "denied"))
