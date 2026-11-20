"""Builds recipe bundles for tests, with the Unix modes a real one carries."""
from __future__ import annotations

import io
import zipfile

RECIPE_MD = "---\ndescription: Use when testing.\n---\n# Blog\nDo X.\n"


def zip_bytes(entries) -> bytes:
    """`entries`: (name, bytes, unix_mode) tuples."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            zf.writestr(info, data)
    return buffer.getvalue()


def recipe_zip(*extra) -> bytes:
    return zip_bytes([("RECIPE.md", RECIPE_MD.encode(), 0o100644),
                      ("scripts/run.sh", b"#!/bin/sh\necho hi\n", 0o100755), *extra])
