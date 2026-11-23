from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

from host.core.constants import SERVICE_API_URL
from host.core.events import (
    ERROR_CODES,
    EventSender,
    error_code,
    events_base,
    sanitize,
)
from host.core.install import InstallError
from host.core.provider import VmUnresponsive

CONTEXT = {"app_version": "0.9.1", "os": "windows", "os_version": "10.0.26100", "arch": "AMD64"}


def test_sanitize_strips_a_windows_user_folder_with_a_space():
    text = r"cannot open C:\Users\Ana Maria\AppData\Local\Eggie\vm.vhdx for ana@x.io"
    out = sanitize(text, home=r"C:\Users\Ana Maria", user="Ana Maria")
    assert "Ana" not in out and "Maria" not in out and "ana@x.io" not in out
    assert out.startswith("cannot open ~")


def test_sanitize_hides_a_home_folder_in_escaped_and_wsl_forms_when_user_differs():
    home, user = r"C:\Users\Ana Maria", "amaria"
    err = subprocess.CalledProcessError(1, [r"C:\Users\Ana Maria\AppData\Local\Eggie\vm"])
    for text in (str(err), "failed /mnt/c/Users/Ana Maria/x"):
        out = sanitize(text, home=home, user=user)
        assert "Ana" not in out and "Maria" not in out, out


def test_sanitize_hides_other_absolute_paths_and_caps_length():
    out = sanitize("bad /opt/x/y.sh and " + "z" * 400, home="/home/u", user="u")
    assert "<path>" in out and len(out) <= 200


def test_error_code_uses_the_install_errors_cause_and_hresult():
    cause = VmUnresponsive("Wsl/Service/CreateInstance/0x8007274C")
    try:
        try:
            raise cause
        except VmUnresponsive as e:
            raise InstallError("create_vm", "failed") from e
    except InstallError as wrapped:
        assert error_code(wrapped) == "vm_unresponsive:0x8007274c"


def test_error_code_falls_back_to_the_class_name():
    assert error_code(KeyError("x")) == "KeyError"


def test_source_run_sends_nothing_without_the_env_var():
    assert events_base(frozen=False, env={}) is None
    assert events_base(frozen=False, env={"EGGIE_EVENTS_URL": "http://s/api"}) == "http://s/api"
    assert events_base(frozen=True, env={}) == SERVICE_API_URL


def test_sender_posts_the_contract_body():
    seen = []
    sender = EventSender("http://s/api", CONTEXT, post=lambda u, b: seen.append((u, json.loads(b))))
    sender.send("app_launch", {"background": False})
    sender.flush()
    assert seen == [("http://s/api/v1/desktop-events", {
        "event": "app_launch", "session_id": sender.session_id,
        "properties": {"background": False}, "context": CONTEXT})]


def test_flush_gives_up_after_its_timeout_when_the_service_hangs():
    release = threading.Event()
    sender = EventSender("http://s/api", CONTEXT, post=lambda u, b: release.wait())
    sender.send("app_launch", {"background": False})
    t0 = time.monotonic()
    sender.flush(timeout=0.2)
    elapsed = time.monotonic() - t0
    release.set()
    assert elapsed < 1.0


def test_a_failing_post_never_reaches_the_caller():
    def boom(url, body):
        raise OSError("down")

    sender = EventSender("http://s/api", CONTEXT, post=boom)
    sender.send("app_launch", {"background": True})
    sender.flush()


def test_error_codes_match_the_public_doc():
    doc = Path(__file__).resolve().parents[2] / "docs" / "desktop-events.md"
    table = doc.read_text(encoding="utf-8").split("| Code |", 1)[1]
    parsed = set(re.findall(r"^\| `([a-z0-9_]+)` \|", table, re.M))
    assert parsed and parsed == ERROR_CODES
