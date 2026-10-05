from __future__ import annotations

import errno
import os
from pathlib import Path


def usage(path: Path, *, statvfs=os.statvfs) -> dict:
    target = Path(path)
    while not target.exists() and target != target.parent:
        target = target.parent
    st = statvfs(str(target))
    return {"free_bytes": st.f_bavail * st.f_frsize,
            "total_bytes": st.f_blocks * st.f_frsize}


def is_disk_full(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT)
