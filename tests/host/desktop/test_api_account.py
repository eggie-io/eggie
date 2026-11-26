"""The account half of the bridge: sign-in links, one-shot flags, the cached kitchen list."""
from __future__ import annotations

import threading
import time

from host.client import ApiError, ApiUnavailableError
from host.core.cloud.account import NotSignedIn, SignIn
from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi

READY = Readiness(vm_exists=True, vm_reachable=True,
                  runtime_version="runtime-v0.1.0", api_version=1)
DEVICE_URL = "https://app.eggie.io/device?user_code=ZXHW-X65F"


class FakeAccount:
    def __init__(self, signed_in=True, outcomes=()):
        self.signed_in = signed_in
        self.email = "a@b.c"
        self.first_name = ""
        self.pending = None
        self.outcomes = list(outcomes)
        self.starts = 0

    def start(self):
        self.starts += 1
        if self.pending is None:
            self.pending = SignIn("ZXHW-X65F", DEVICE_URL, time.time() + 600, 0.0)
        return self.pending

    def poll_once(self):
        if not self.outcomes:
            time.sleep(0.01)
            return "pending"
        outcome = self.outcomes.pop(0)
        if outcome != "pending":
            self.pending = None
            self.signed_in = outcome == "signed_in"
        return outcome

    def sign_out(self):
        self.signed_in = False


class FakeClient:
    def __init__(self, *results):
        self.results = list(results)

    def instances(self):
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _api(tmp_path, account, client=None, events=None, pushed=None):
    return DesktopApi(object(), InstallState(tmp_path / "s.json"),
                      push=(pushed if pushed is not None else []).append,
                      probe_fn=lambda provider: READY,
                      cloud=(account, client or FakeClient()), sleep=lambda s: None,
                      events=events.sender if events else None)


def _kitchen(id="k1"):
    return {"id": id, "is_owner": True, "status": "running", "created_at": "2026-01-01"}


def _wait_for(pushed, count=1):
    deadline = time.monotonic() + 5
    while len(pushed) < count and time.monotonic() < deadline:
        time.sleep(0.01)


def test_create_account_link_goes_through_signup(tmp_path):
    out = _api(tmp_path, FakeAccount(signed_in=False)).start_sign_in("create")
    assert out["url"] == ("https://app.eggie.io/signup?redirect="
                          "%2Fdevice%3Fuser_code%3DZXHW-X65F")
    assert out["user_code"] == "ZXHW-X65F"


def test_existing_account_link_is_the_device_page_itself(tmp_path):
    assert _api(tmp_path, FakeAccount(signed_in=False)).start_sign_in("existing")["url"] == DEVICE_URL


def test_signup_events_are_sent_once(tmp_path, events):
    api = _api(tmp_path, FakeAccount(signed_in=False, outcomes=[]), events=events)
    api.account_screen_shown()
    api.account_screen_shown()
    api.start_sign_in("create")
    api.start_sign_in("create")
    assert events.sent(at_least=2) == [("signup_screen_shown", {}),
                                       ("signup_started", {"method": "new"})]


def test_approved_sign_in_keeps_the_method_that_started_it(tmp_path, events):
    pushed = []
    account = FakeAccount(signed_in=False, outcomes=[])
    api = _api(tmp_path, account, events=events, pushed=pushed)
    api.start_sign_in("create")
    api.start_sign_in("existing")
    account.outcomes.append("signed_in")
    _wait_for(pushed)
    assert events.sent(at_least=2)[-1] == ("signup_completed", {"method": "new"})


def test_denied_sign_in_is_not_reported_as_completed(tmp_path, events):
    pushed = []
    api = _api(tmp_path, FakeAccount(signed_in=False, outcomes=["denied"]),
               events=events, pushed=pushed)
    api.start_sign_in("create")
    _wait_for(pushed)
    assert [name for name, _ in events.sent(at_least=1)] == ["signup_started"]


def test_poller_reports_the_outcome_and_the_account_screen_ends(tmp_path):
    pushed = []
    api = _api(tmp_path, FakeAccount(signed_in=False, outcomes=["pending", "denied"]),
               pushed=pushed)
    api.start_sign_in("existing")
    _wait_for(pushed)
    assert pushed == [{"kind": "account", "type": "denied"}]


def test_poller_survives_a_failing_poll(tmp_path):
    pushed = []
    account = FakeAccount(signed_in=False, outcomes=[RuntimeError("boom"), "signed_in"])
    original = account.poll_once

    def poll_once():
        if account.outcomes and isinstance(account.outcomes[0], Exception):
            raise account.outcomes.pop(0)
        return original()

    account.poll_once = poll_once
    _api(tmp_path, account, pushed=pushed).start_sign_in("existing")
    _wait_for(pushed)
    assert pushed == [{"kind": "account", "type": "signed_in"}]


def test_both_sign_in_buttons_pressed_at_once_mint_one_code(tmp_path):
    account = FakeAccount(signed_in=False)
    minted = []

    def slow_start():
        if account.pending is None:
            time.sleep(0.05)
            minted.append(1)
            account.pending = SignIn(f"CODE-{len(minted)}", DEVICE_URL, time.time() + 600, 0.0)
        return account.pending

    account.start = slow_start
    api = _api(tmp_path, account)
    gate = threading.Barrier(2)
    codes = []

    def press(mode):
        gate.wait()
        codes.append(api.start_sign_in(mode)["user_code"])

    threads = [threading.Thread(target=press, args=(m,)) for m in ("create", "existing")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert (len(minted), codes) == (1, ["CODE-1", "CODE-1"])


def test_home_after_first_sign_in_goes_to_connect_once(tmp_path):
    pushed = []
    api = _api(tmp_path, FakeAccount(signed_in=False, outcomes=["signed_in"]),
               FakeClient([_kitchen("k1")], [_kitchen("k1")]), pushed=pushed)
    api.start_sign_in("existing")
    _wait_for(pushed)
    first = api.home()
    assert first["action"] == "load_kitchens"
    api.load_kitchens()
    home = api.home()
    assert (home["screen"], home["kitchen_id"]) == ("connect", "k1")
    assert api.home()["screen"] == "home"


def test_kitchen_fetch_failure_keeps_the_last_list(tmp_path):
    api = _api(tmp_path, FakeAccount(),
               FakeClient([_kitchen()], ApiUnavailableError("down"), ApiError("x", "Nope", 500)))
    api.load_kitchens()
    assert api.load_kitchens() == {"ok": False}
    home = api.home()
    assert home["screen"] == "home"
    assert [c["id"] for c in home["home"]["cards"] if c["kind"] == "cloud"] == ["k1"]
    assert home["home"]["cloud_error"] == "Can't reach Eggie right now."
    api.load_kitchens()
    assert api.home()["home"]["cloud_error"] == "Nope"


def test_a_401_clears_the_list_so_home_asks_for_sign_in(tmp_path):
    account = FakeAccount()
    api = _api(tmp_path, account, FakeClient([_kitchen()], NotSignedIn()))
    api.load_kitchens()
    api.load_kitchens()
    account.signed_in = False
    assert api.home()["screen"] == "account"


def test_sign_out_drops_the_cached_kitchens(tmp_path):
    api = _api(tmp_path, FakeAccount(), FakeClient([_kitchen()]))
    api.load_kitchens()
    api.sign_out()
    assert api.home()["screen"] == "account"


class CheckoutClient(FakeClient):
    def instance_catalog(self):
        return {"regions": [{"id": "europe", "label": "Europe", "enabled": True,
                             "size_ids_with_room": ["small"]}],
                "sizes": [{"id": "small", "label": "S", "description": "", "vcpus": 1,
                           "memory_gb": 2, "disk_gb": 40, "enabled": True}]}

    def billing_catalog(self):
        return {"enabled": False, "sizes": [], "trial_days": None}


def test_a_cancelled_payment_waits_until_back_and_back_ends_it(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("host.desktop.api.webbrowser.open", opened.append)
    old = _kitchen("old")
    api = _api(tmp_path, FakeAccount(), CheckoutClient([old], [old], [old]))
    now = [1000.0]
    api._clock = lambda: now[0]
    assert api.catalog()["name"] == "My cloud"
    assert api.start_checkout("Bakery", "small", "europe") == {"ok": True}
    assert len(opened) == 1
    now[0] += 86400
    assert api.checkout_status() == {"state": "waiting", "kitchen_id": "", "wait": 10.0}
    api.cancel_checkout()
    assert api.checkout_status()["state"] == "none"
    assert api.open_checkout_page() == {"ok": False} and len(opened) == 1


def test_checkout_refuses_a_size_the_catalog_did_not_offer(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("host.desktop.api.webbrowser.open", opened.append)
    api = _api(tmp_path, FakeAccount(), CheckoutClient([]))
    api.catalog()
    assert api.start_checkout("Bakery", "large", "europe")["ok"] is False
    assert opened == []
