from __future__ import annotations

import hashlib
import http.client
import urllib.error
from pathlib import Path

from .images import Image

_CHUNK = 1024 * 1024

# Applies per socket operation, not to the whole 391 MB transfer: a stalled
# connection raises instead of hanging the installer with no way to cancel.
_TIMEOUT_SECONDS = 60


class ChecksumMismatch(RuntimeError):
    """The downloaded bytes do not match the expected digest."""

    code = "checksum_mismatch"


class DownloadFailed(RuntimeError):
    """The network dropped, timed out or refused the download."""

    code = "download_failed"


# URLError, timeouts and resets are all OSErrors, as is a full disk, which
# must keep its own type. A drop mid-body raises IncompleteRead, not OSError.
_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError,
                   http.client.HTTPException)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_opener(url: str, start_byte: int):
    from urllib.request import Request, urlopen
    from . import tls
    headers = {"Range": f"bytes={start_byte}-"} if start_byte else {}
    response = urlopen(Request(url, headers=headers), timeout=_TIMEOUT_SECONDS,
                       context=tls.context())
    length = int(response.headers.get("Content-Length", 0)) + start_byte
    return response, length


def fetch(image: Image, dest: Path, *, opener=_default_opener,
          on_progress=None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and sha256_of(dest) == image.sha256:
        return dest

    part = dest.with_suffix(dest.suffix + ".part")
    start = part.stat().st_size if part.exists() else 0
    try:
        stream, total = opener(image.url, start)
        with open(part, "ab") as out:
            while chunk := stream.read(_CHUNK):
                out.write(chunk)
                start += len(chunk)
                if on_progress:
                    on_progress(start, total)
    except _NETWORK_ERRORS as e:
        raise DownloadFailed(str(e)) from e

    actual_digest = sha256_of(part)
    if actual_digest != image.sha256:
        part.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        raise ChecksumMismatch(
            f"{dest.name}: expected {image.sha256}, got {actual_digest}")

    part.replace(dest)
    return dest
