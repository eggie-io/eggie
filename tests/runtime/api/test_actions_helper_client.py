import json
import shutil
import socketserver
import struct
import tempfile
import threading
import time
from pathlib import Path

import pytest

from eggie_api.infra.actions_helper import (
    ActionsHelper, HelperError, HelperUnavailable, connection_target, project_target)

APP = project_target("app", "ios-build")
RUN = {"state": "running", "exit_code": None, "started_at": 1700000000}


class Helper(socketserver.ThreadingUnixStreamServer):
    """Speaks eggie-actions' wire: one JSON line each way, then frames after an attach."""
    daemon_threads = True

    def __init__(self, path):
        self.requests: list[dict] = []
        self.answers: dict[str, dict] = {}
        self.delay = 0.0
        self.frames: list[tuple[int, bytes]] = []
        self.got_frames = threading.Event()
        super().__init__(path, Handler)


class Handler(socketserver.StreamRequestHandler):
    server: Helper

    def handle(self):
        request = json.loads(self.rfile.readline())
        self.server.requests.append(request)
        time.sleep(self.server.delay)
        answer = self.server.answers.get(request["op"], {"ok": True})
        self.wfile.write(json.dumps(answer).encode() + b"\n")
        if request["op"] != "attach" or not answer["ok"]:
            return
        self.wfile.write(struct.pack("!BI", 0, 5) + b"hello")
        self.wfile.write(struct.pack("!BI", 0, 6) + b" world")
        for _ in range(2):
            kind, length = struct.unpack("!BI", self.rfile.read(5))
            self.server.frames.append((kind, self.rfile.read(length)))
        self.server.got_frames.set()


@pytest.fixture
def helper():
    # A short directory of its own: unix socket paths stop at 108 bytes.
    folder = Path(tempfile.mkdtemp(prefix="ea"))
    server = Helper(str(folder / "s"))
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield server, ActionsHelper(folder / "s", timeout=2)
    server.shutdown()
    server.server_close()
    shutil.rmtree(folder, ignore_errors=True)


def test_each_op_sends_one_request_line_of_its_own_shape(helper):
    server, client = helper
    server.answers = {"start": {"ok": True, "run": RUN}, "stop": {"ok": True, "run": None},
                      "status": {"ok": True, "runs": {"ios-build": RUN}}}
    assert client.start(APP, "eas build", log=True) == RUN
    assert client.stop(APP) is None
    assert client.status("app") == {"ios-build": RUN}
    assert client.stop_project("app") is None
    assert server.requests == [
        {"op": "start", "project": "app", "action": "ios-build", "command": "eas build",
         "log": True},
        {"op": "stop", "project": "app", "action": "ios-build"},
        {"op": "status", "project": "app"},
        {"op": "stop-project", "project": "app"},
    ]


def test_a_refusal_carries_the_helpers_code_and_message(helper):
    server, client = helper
    server.answers = {"start": {"ok": False, "code": "action_running",
                                "message": "ios-build is already running"}}
    with pytest.raises(HelperError) as caught:
        client.start(APP, "eas build", log=True)
    assert (caught.value.code, caught.value.message) == (
        "action_running", "ios-build is already running")


def test_no_helper_listening_is_unavailable(tmp_path):
    with pytest.raises(HelperUnavailable):
        ActionsHelper(tmp_path / "missing.sock", timeout=1).status("app")


def test_a_stop_may_take_longer_than_other_requests(tmp_path):
    folder = Path(tempfile.mkdtemp(prefix="ea"))
    server = Helper(str(folder / "s"))
    server.delay = 0.5
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    try:
        client = ActionsHelper(folder / "s", timeout=0.2)
        assert client.stop(APP) is None
        client.stop_project("app")
        with pytest.raises(HelperUnavailable):
            client.status("app")
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(folder, ignore_errors=True)


def test_attach_relays_terminal_bytes_and_resizes_both_ways(helper):
    server, client = helper
    terminal = client.attach(APP, 120, 32)
    assert server.requests[-1] == {"op": "attach", "project": "app", "action": "ios-build",
                                   "cols": 120, "rows": 32}
    received = b""
    while len(received) < len(b"hello world"):
        received += terminal.read()
    assert received == b"hello world"
    terminal.write(b"y\n")
    terminal.resize(80, 24)
    assert server.got_frames.wait(2)
    assert server.frames == [(0, b"y\n"), (1, struct.pack("!HH", 80, 24))]
    assert terminal.read() == b""
    terminal.close()


def test_attach_to_nothing_running_is_a_refusal(helper):
    server, client = helper
    server.answers = {"attach": {"ok": False, "code": "not_running", "message": "no run"}}
    with pytest.raises(HelperError) as caught:
        client.attach(APP, 80, 24)
    assert caught.value.code == "not_running"


def test_output_reads_a_whole_scrollback_longer_than_a_status_reply(helper):
    server, client = helper
    text = "".join(f"line-{i}\n" for i in range(20000))
    server.answers = {"output": {"ok": True, "text": text}}
    assert client.output(APP) == text
    assert server.requests == [{"op": "output", "project": "app", "action": "ios-build"}]


def test_a_screen_of_wide_lines_longer_than_a_status_reply_is_read_whole(helper):
    server, client = helper
    # Non-ASCII is JSON-escaped six bytes a character.
    text = "\n".join("є" * 300 for _ in range(232))
    screen = {"rows": [], "cursor_y": 0, "history_size": 0, "text": text, "cursor_line": ""}
    server.answers = {"screen": {"ok": True, **screen}}
    assert client.screen(APP) == screen


def test_connection_calls_send_the_connection_target_and_a_deadline(helper):
    server, client = helper
    server.answers = {"screen": {"ok": True, "rows": 24, "cursor_y": 3, "history_size": 0,
                                 "text": "Password:"}}
    github = connection_target("github")
    client.start(github, "gh auth login", log=False, deadline=900)
    client.run(github)
    assert client.screen(github) == {"rows": 24, "cursor_y": 3, "history_size": 0,
                                     "text": "Password:"}
    client.type(github, "hunter2")
    assert server.requests == [
        {"op": "start", "connection": "github", "command": "gh auth login", "log": False,
         "deadline": 900},
        {"op": "run", "connection": "github"},
        {"op": "screen", "connection": "github"},
        {"op": "type", "connection": "github", "text": "hunter2"},
    ]


def test_check_returns_the_exit_code_and_none_when_the_helper_has_none(helper):
    server, client = helper
    server.answers = {"check": {"ok": True, "exit_code": 1}}
    assert client.check("gh auth status") == 1
    server.answers = {"check": {"ok": True, "exit_code": None}}
    assert client.check("gh auth status") is None
    server.answers = {"check": {"ok": True}}
    with pytest.raises(HelperError):
        client.check("gh auth status")
