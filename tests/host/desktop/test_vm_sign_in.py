"""The VM's own sign-in, and how the install carries on around it."""
from __future__ import annotations

from host.client import ApiUnavailableError
from host.core.install import InstallState, Step
from host.core.status import Readiness
from host.desktop.api import DesktopApi
from host.desktop.vm_sign_in import sign_in_vm


class FakeClient:
    def __init__(self, statuses, sign_in=None):
        self.statuses = list(statuses)
        self.sign_in_reply = sign_in or {"state": "pending", "url": "https://x/approve"}
        self.signed_in_calls = 0

    def account(self):
        item = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(item, Exception):
            raise item
        return item

    def account_sign_in(self):
        self.signed_in_calls += 1
        return self.sign_in_reply


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def __call__(self):
        return self.now


def _run(client, **kw):
    clock, opened = Clock(), []
    outcome = sign_in_vm(client, open_url=opened.append, sleep=clock.sleep,
                         monotonic=clock, **kw)
    return outcome, opened, clock


def test_signed_in_vm_opens_nothing():
    client = FakeClient([{"state": "signed_in"}])
    outcome, opened, _ = _run(client)
    assert (outcome, opened, client.signed_in_calls) == ("signed_in", [], 0)


def test_waits_for_approval_then_finishes():
    client = FakeClient([{"state": "signed_out"}, {"state": "pending"}, {"state": "signed_in"}])
    outcome, opened, clock = _run(client)
    assert (outcome, opened, clock.now) == ("signed_in", ["https://x/approve"], 4.0)


def test_nobody_approving_times_out_at_the_limit():
    client = FakeClient([{"state": "signed_out"}, {"state": "pending"}])
    outcome, opened, clock = _run(client, limit=600.0)
    assert outcome == "timed_out" and len(opened) == 1 and clock.now >= 600.0


def test_a_refusal_after_asking_stops_waiting():
    client = FakeClient([{"state": "signed_out"}, {"state": "signed_out", "error": "access_denied"}])
    outcome, _, _ = _run(client)
    assert outcome == "refused"


def test_install_finishes_when_nobody_approves(tmp_path):
    pushed, opened, clock = [], [], Clock()
    client = FakeClient([{"state": "signed_out"}, {"state": "pending"}])
    steps = [Step("connect", lambda: None, always_run=True), Step("verify", lambda: None)]
    api = DesktopApi(object(), InstallState(tmp_path / "s.json"), push=pushed.append,
                     probe_fn=lambda p: Readiness(), steps_factory=lambda: steps,
                     client_factory=lambda p: client, open_url=opened.append,
                     sleep=clock.sleep, clock=clock)
    api.start_install()
    api.jobs.join(timeout=5)

    rows = [(e["step"], e["status"], e["message"]) for e in pushed if e["type"] == "step"]
    assert rows[:4] == [("connect", "running", ""),
                        ("connect", "running", "Press Approve in your browser"),
                        ("connect", "done", ""), ("verify", "running", "")]
    assert pushed[-1]["type"] == "done"


def test_a_failing_poll_does_not_end_the_wait():
    client = FakeClient([{"state": "signed_out"}, ApiUnavailableError("down"), {"state": "signed_in"}])
    outcome, _, _ = _run(client)
    assert outcome == "signed_in"


def test_an_already_signed_in_vm_never_shows_the_approve_prompt(tmp_path):
    pushed = []
    client = FakeClient([{"state": "signed_in"}])
    steps = [Step("connect", lambda: None, always_run=True)]
    api = DesktopApi(object(), InstallState(tmp_path / "s.json"), push=pushed.append,
                     probe_fn=lambda p: Readiness(), steps_factory=lambda: steps,
                     client_factory=lambda p: client, open_url=lambda u: None,
                     sleep=lambda s: None)
    api.start_install()
    api.jobs.join(timeout=5)
    assert not [e for e in pushed if e.get("message") == "Press Approve in your browser"]
