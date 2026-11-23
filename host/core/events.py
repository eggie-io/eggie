"""Anonymous install and onboarding events sent to the Eggie service."""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import urllib.request
import uuid
from collections.abc import Callable, Mapping

from host.core.constants import SERVICE_API_URL

log = logging.getLogger(__name__)

ERROR_CODES = frozenset({
    "windows_build_too_old", "cpu_virtualization_off", "low_disk_space", "macos_too_old",
    "elevation_declined", "wsl_features_failed", "wsl_update_failed", "download_failed",
    "checksum_mismatch", "lima_unsupported_cpu", "lima_archive_invalid", "lima_not_runnable",
    "lima_wrong_version", "rootfs_missing", "vm_import_failed", "vm_create_failed",
    "vm_configure_failed", "vm_start_failed", "vm_stop_failed", "vm_unresponsive",
    "port_forward_failed", "ssh_setup_failed", "bootstrap_failed", "bootstrap_incomplete",
    "bootstrap_bad_source", "api_unreachable", "api_incompatible", "api_not_accepted",
    "api_error", "verify_no_answer", "verify_http_error", "verify_not_running",
    "verify_no_address", "verify_cleanup_failed", "job_timeout", "job_failed",
    "vm_remove_failed", "wsl_restart_failed", "vm_running", "webview_unavailable", "uncaught",
})

_HRESULT = re.compile(r"0x[0-9a-fA-F]{8}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Drive paths and POSIX absolute paths; "~", ":" and word chars before "/"
# keep "~/x", "http://h" and "and/or" intact.
_PATH = re.compile(r"\b[A-Za-z]:[\\/]\S*|(?<![\w~:.])/\S*")
_MAX_MESSAGE = 200


def error_code(exc: BaseException) -> str:
    from host.core.install import InstallError

    root = exc
    if isinstance(exc, InstallError) and exc.__cause__ is not None:
        root = exc.__cause__
    code = getattr(root, "code", None)
    if not isinstance(code, str) or not code:
        code = type(root).__name__
    hresult = _HRESULT.search(str(root))
    return f"{code}:{hresult.group(0).lower()}" if hresult else code


def sanitize(text: str, *, home: str, user: str) -> str:
    # A home with a space ("Ana Maria") must go as a whole, before the
    # username and the generic path rule can split it.
    for variant in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
        if variant:
            text = re.sub(re.escape(variant), "~", text, flags=re.I)
    text = _EMAIL.sub("<email>", text)
    if user:
        text = re.sub(rf"(?<!\w){re.escape(user)}(?!\w)", "<user>", text, flags=re.I)
    text = _PATH.sub("<path>", text)
    return " ".join(text.split())[:_MAX_MESSAGE]


def events_base(*, frozen: bool, env: Mapping[str, str]) -> str | None:
    if frozen:
        return SERVICE_API_URL
    return env.get("EGGIE_EVENTS_URL") or None


def _http_post(url: str, body: bytes) -> None:
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "Eggie-Desktop"})
    with urllib.request.urlopen(req, timeout=3):
        pass


class EventSender:
    def __init__(self, base: str | None, context: dict[str, str], *,
                 session_id: str | None = None,
                 post: Callable[[str, bytes], None] | None = None):
        self._base = base
        self._context = dict(context)
        self._post = post or _http_post
        self.session_id = session_id or str(uuid.uuid4())
        self.enabled = base is not None
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def send(self, event: str, properties: dict | None = None) -> None:
        if not self.enabled:
            return
        try:
            body = json.dumps({
                "event": event, "session_id": self.session_id,
                "properties": properties or {}, "context": self._context,
            }).encode("utf-8")
            self._start()
            self._queue.put(body)
        except Exception:
            log.debug("event %s not queued", event, exc_info=True)

    def flush(self, timeout: float = 2.0) -> None:
        # A hung post must not hold up exit, so wait on a helper we can abandon.
        waiter = threading.Thread(target=self._queue.join, daemon=True)
        waiter.start()
        waiter.join(timeout)

    def _start(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def _run(self) -> None:
        url = f"{self._base}/v1/desktop-events"
        while True:
            body = self._queue.get()
            try:
                self._post(url, body)
            except Exception:
                log.debug("event post failed", exc_info=True)
            finally:
                self._queue.task_done()
