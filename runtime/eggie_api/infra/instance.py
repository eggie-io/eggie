from __future__ import annotations

import http.client
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

# A proxy's own error page, not the instance: the instance always answers with
# its JSON error body.
_GATEWAY = {502, 503, 504}


class InstanceError(Exception):
    def __init__(self, code: str, message: str, status: int, body: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.body = body or {}


class InstanceUnreachable(Exception):
    pass


class _ProgressFile:
    """File body that reports bytes read; urllib streams it by `read()`."""

    def __init__(self, path: Path, on_progress: Callable[[int], None]):
        self._file = open(path, "rb")
        self._on_progress = on_progress
        self._sent = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._file.read(size)
        if chunk:
            self._sent += len(chunk)
            self._on_progress(self._sent)
        return chunk

    def close(self) -> None:
        self._file.close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would re-send the Eggie-Transfer-* headers to wherever it points."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def _default_opener(ca_file: Path | None):
    context = ssl.create_default_context(cafile=None)
    if ca_file is not None:
        context.load_verify_locations(cafile=str(ca_file))
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context),
                                       _NoRedirect()).open


def _envelope(raw: bytes) -> dict | None:
    try:
        error = json.loads(raw)["error"]
        return {"code": str(error["code"]), "message": str(error["message"])}
    except (ValueError, KeyError, TypeError):
        return None


class InstanceClient:
    def __init__(self, *, opener=None, ca_file: Path | None = None,
                 timeout: float = 30.0):
        self._open = opener or _default_opener(ca_file)
        # Applies to each socket operation, so a multi-GB upload is fine as
        # long as bytes keep moving.
        self._timeout = timeout

    def _call(self, method: str, host: str, path: str, headers: dict,
              body=None, *, older_is_unreachable: bool = False):
        request = urllib.request.Request(f"https://{host}{path}", data=body,
                                         headers=headers, method=method)
        try:
            with self._open(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                raise InstanceUnreachable(f"the kitchen answered {e.code}") from None
            raise self._error(e.code, e.read(), older_is_unreachable) from None
        except (OSError, http.client.HTTPException) as e:
            raise InstanceUnreachable(str(e)) from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise InstanceError("bad_response",
                               "the kitchen sent something that is not JSON",
                               200) from None

    @staticmethod
    def _error(status: int, raw: bytes, older: bool) -> Exception:
        error = _envelope(raw)
        # An older instance has no transfer route: it 404s, or its browser
        # checks (Origin, then sign-in) answer first.
        if older and (status == 404 or (error and error["code"] in (
                "not_signed_in", "forbidden_origin"))):
            return InstanceUnreachable("older")
        if error:
            return InstanceError(error["code"], error["message"], status, error)
        if status in _GATEWAY:
            return InstanceUnreachable(f"the kitchen answered {status}")
        text = raw.decode("utf-8", errors="replace").strip()[:200]
        return InstanceError(f"http_{status}", text or f"status {status}", status)

    def open(self, host: str, *, ticket: str, size: int, data: bool,
             replace: str) -> dict:
        return self._call("POST", host, "/api/transfers", {
            "Accept": "application/json",
            "Eggie-Transfer-Ticket": ticket,
            "Eggie-Transfer-Size": str(size),
            "Eggie-Transfer-Data": "yes" if data else "no",
            "Eggie-Transfer-Replace": replace,
        }, older_is_unreachable=True)

    def upload(self, host: str, push_id: str, token: str, path: Path,
               on_progress: Callable[[int], None]) -> None:
        size = path.stat().st_size
        body = _ProgressFile(path, on_progress)
        try:
            self._call("PUT", host,
                       f"/api/transfers/{urllib.parse.quote(push_id, safe='')}/bundle",
                       {"Accept": "application/json",
                        "Eggie-Transfer-Token": token,
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(size)}, body)
        finally:
            body.close()

    def status(self, host: str, push_id: str, token: str) -> dict:
        return self._call("GET", host,
                          f"/api/transfers/{urllib.parse.quote(push_id, safe='')}",
                          {"Accept": "application/json",
                           "Eggie-Transfer-Token": token})
