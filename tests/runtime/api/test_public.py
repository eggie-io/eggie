import os
import stat

import pytest

from omelet_api.core.account import Account
from omelet_api.core.cloud import CloudError, CloudUnavailable
from omelet_api.core.exec import Completed
from omelet_api.core.public import (MESSAGES, Public, PublicBusy, TunnelClient,
                                    Unavailable, write_token)
from omelet_api.core.state import State
from tests.runtime.api.fake_cloud import FakeCloud

HOSTS = [{"service": "web", "hostname": "blog.d.io", "local_url": "http://blog.d.io:39080"}]
ON = {"id": "u1", "project_id": "c-blog",
      "urls": [{"service": "web", "local_hostname": "blog.d.io",
                "url": "https://k3x9.example.dev"}],
      "expires_at": "2026-09-24T15:00:00Z",
      "credentials": {"provider": "cloudflare", "token": "tun-1"}}
EXPIRES = 1790262000.0  # 2026-09-24T15:00:00Z


class Clock:
    def __init__(self, now=EXPIRES - 3600):
        self.now = now

    def __call__(self):
        return self.now


class TunnelRunner:
    """Answers the three compose calls TunnelClient makes; records argv."""

    def __init__(self, start_ok=True):
        self.calls = []
        self.start_ok = start_ok
        self.up = False

    def exec(self, argv, *, root=False):
        self.calls.append(argv)
        if "up" in argv:
            self.up = self.start_ok
            return Completed(0 if self.start_ok else 1, "", "" if self.start_ok else "boom")
        if "rm" in argv:
            self.up = False
            return Completed(0, "", "")
        if "ps" in argv:
            return Completed(0, "abc\n" if self.up else "", "")
        return Completed(0, "", "")


def make(tmp_path, cloud, *, hosts=HOSTS, start_ok=True, projects=("blog",),
         signed_in=True, mapped=("blog",)):
    state = State(tmp_path / "state.db")
    for pid in projects:
        state.add_project(pid, f"/p/{pid}", "d.io")
    if signed_in:
        state.update_account(access_token="at", refresh_token="rt",
                             access_expires_at=10**12, org_id="org-1")
    for pid in mapped:
        state.map_cloud_project(pid, f"c-{pid}", "org-1")
    account = Account(state, cloud, spawn=lambda fn: None)
    runner = TunnelRunner(start_ok)
    clock = Clock()
    public = Public(state=state, account=account, cloud=cloud,
                    client=TunnelClient(runner, tmp_path / "stack.yml"),
                    token_path=tmp_path / "tunnel.token", origin="http://traefik:39080",
                    hosts_for=lambda pid: list(hosts), clock=clock,
                    spawn=lambda fn: fn())
    return public, state, runner, clock


def test_turning_on_writes_a_narrow_token_starts_the_client_and_shows_the_urls(tmp_path):
    cloud = FakeCloud(create_public_url=[ON])
    public, _, runner, _ = make(tmp_path, cloud)

    public.enable("blog")

    token = tmp_path / "tunnel.token"
    assert token.read_text() == "tun-1"
    assert stat.S_IMODE(os.stat(token).st_mode) == 0o640
    assert runner.up
    assert cloud.calls == [("create_public_url", "at", "c-blog",
                            [{"local_hostname": "blog.d.io", "service": "web"}],
                            "http://traefik:39080")]
    assert public.status("blog") == {
        "state": "on", "expires_at": EXPIRES,
        "urls": [{"url": "https://k3x9.example.dev", "service": "web",
                  "local_url": "http://blog.d.io:39080"}]}


@pytest.mark.parametrize("kwargs,code", [
    ({"signed_in": False}, "signed_out"),
    ({"mapped": ()}, "not_registered"),
    ({"hosts": []}, "no_web"),
])
def test_turning_on_is_refused_with_a_plain_reason(tmp_path, kwargs, code):
    public, _, _, _ = make(tmp_path, FakeCloud(), **kwargs)

    with pytest.raises(Unavailable) as raised:
        public.enable("blog")

    assert (raised.value.code, raised.value.message) == (code, MESSAGES[code])
    assert public.status("blog") == {"state": "unavailable",
                                     "reason": {"code": code, "message": MESSAGES[code]}}


def test_one_already_on_in_this_vm_is_named(tmp_path):
    cloud = FakeCloud(create_public_url=[ON, CloudError("public_url_active", "x", 409)])
    public, _, _, _ = make(tmp_path, cloud, projects=("blog", "shop"), mapped=("blog", "shop"))
    public.enable("blog")

    public.enable("shop")

    assert public.status("shop") == {"state": "failed", "reason": {
        "code": "public_url_active",
        "message": 'Only one public address can be on at a time. Turn off the one on "blog" first.'}}


def test_one_already_on_elsewhere_says_so(tmp_path):
    cloud = FakeCloud(create_public_url=[CloudError("public_url_active", "x", 409)])
    public, _, _, _ = make(tmp_path, cloud)

    public.enable("blog")

    assert public.status("blog")["reason"]["message"] == MESSAGES["public_url_active"]


def test_an_unknown_service_refusal_shows_the_services_own_message(tmp_path):
    cloud = FakeCloud(create_public_url=[CloudError("weird", "try later", 400)])
    public, _, _, _ = make(tmp_path, cloud)

    public.enable("blog")

    assert public.status("blog")["reason"] == {
        "code": "weird", "message": "The Omelet service refused: try later"}


def test_a_client_that_will_not_start_is_released_on_the_service(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None])
    public, _, runner, _ = make(tmp_path, cloud, start_ok=False)

    public.enable("blog")

    assert cloud.names() == ["create_public_url", "release_public_url"]
    assert not (tmp_path / "tunnel.token").exists()
    assert public.status("blog")["reason"]["code"] == "client_failed"


def test_turning_off_while_the_service_is_down_is_off_here_and_retried(tmp_path):
    cloud = FakeCloud(create_public_url=[ON],
                      release_public_url=[CloudUnavailable("down"), None],
                      get_public_url=[CloudError("not_found", "none", 404)])
    public, _, runner, _ = make(tmp_path, cloud)
    public.enable("blog")

    assert public.disable("blog") == {"state": "off", "note": None}
    assert not runner.up and not (tmp_path / "tunnel.token").exists()

    public.reconcile()

    assert cloud.names()[-3:] == ["release_public_url", "release_public_url",
                                  "get_public_url"]


def test_turning_one_off_keeps_the_client_another_project_needs(tmp_path):
    other = {**ON, "urls": [{"service": "web", "local_hostname": "blog.d.io",
                       "url": "https://z.example.dev"}]}
    cloud = FakeCloud(create_public_url=[ON, other], release_public_url=[None])
    public, _, runner, _ = make(tmp_path, cloud, projects=("blog", "shop"),
                                mapped=("blog", "shop"))
    public.enable("blog")
    public.enable("shop")

    public.disable("blog")

    assert runner.up and (tmp_path / "tunnel.token").exists()


def test_an_enable_dropped_by_a_sign_out_meanwhile_undoes_itself(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None])
    public, _, runner, _ = make(tmp_path, cloud)
    held = []
    public._spawn = held.append
    public.enable("blog")
    public.forget_local()  # a sign-out landed while the service call was in flight

    held[0]()

    assert public.status("blog")["state"] != "on"
    assert not runner.up and not (tmp_path / "tunnel.token").exists()
    assert cloud.names() == ["create_public_url", "release_public_url"]


def test_turning_off_while_turning_on_is_busy(tmp_path):
    public, _, _, _ = make(tmp_path, FakeCloud())
    public._spawn = lambda fn: None
    public.enable("blog")

    with pytest.raises(PublicBusy):
        public.disable("blog")
    with pytest.raises(PublicBusy):
        public.enable("blog")


def test_an_expired_url_reads_as_off_before_anything_cleans_up(tmp_path):
    public, _, _, clock = make(tmp_path, FakeCloud(create_public_url=[ON]))
    public.enable("blog")
    clock.now = EXPIRES

    assert public.status("blog") == {"state": "off", "note": {
        "code": "expired", "message": MESSAGES["expired"]}}


def test_a_turn_off_after_the_client_starts_is_not_undone_by_the_turn_on(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None, None])
    public, _, runner, _ = make(tmp_path, cloud)
    start = runner.exec

    def racing(argv, *, root=False):
        result = start(argv, root=root)
        if "up" in argv:
            public.disable("blog", force=True)
        return result

    runner.exec = racing

    public.enable("blog")

    assert public.status("blog") == {"state": "off", "note": None}
    assert not runner.up and not (tmp_path / "tunnel.token").exists()
    assert cloud.names()[-1] == "release_public_url"


def test_a_malformed_reply_is_cleaned_up_like_a_failed_start(tmp_path):
    bad = {k: v for k, v in ON.items() if k != "expires_at"}
    cloud = FakeCloud(create_public_url=[bad], release_public_url=[None])
    public, _, runner, _ = make(tmp_path, cloud)

    public.enable("blog")

    assert cloud.names() == ["create_public_url", "release_public_url"]
    assert not runner.up
    assert not (tmp_path / "tunnel.token").exists()
    assert public.status("blog")["reason"]["code"] == "client_failed"


def test_reconcile_ends_an_expired_url_and_stops_the_client(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], get_public_url=[ON])
    public, _, runner, clock = make(tmp_path, cloud)
    public.enable("blog")
    clock.now = EXPIRES + 1  # the service may list it for up to a minute more

    public.reconcile()

    assert public.status("blog")["note"]["code"] == "expired"
    assert not runner.up and not (tmp_path / "tunnel.token").exists()


def test_reconcile_notices_a_url_turned_off_from_the_website(tmp_path):
    cloud = FakeCloud(create_public_url=[ON],
                      get_public_url=[CloudError("not_found", "none", 404)])
    public, _, runner, _ = make(tmp_path, cloud)
    public.enable("blog")

    public.reconcile()

    assert public.status("blog")["note"]["code"] == "released_elsewhere"
    assert not runner.up


def test_reconcile_restarts_a_client_that_is_down_while_a_url_is_on(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], get_public_url=[ON])
    public, _, runner, _ = make(tmp_path, cloud)
    public.enable("blog")
    runner.up = False  # the VM rebooted, or someone removed the container

    public.reconcile()

    assert runner.up


def test_reconcile_stops_a_client_nothing_needs(tmp_path):
    cloud = FakeCloud(get_public_url=[CloudError("not_found", "none", 404)])
    public, _, runner, _ = make(tmp_path, cloud)
    runner.up = True
    (tmp_path / "tunnel.token").write_text("stale")

    public.reconcile()

    assert not runner.up and not (tmp_path / "tunnel.token").exists()


def test_reconcile_leaves_the_client_alone_while_a_turn_on_is_in_flight(tmp_path):
    """The turn-on starts the client before it records the URL; a reconcile
    in between must not stop the client out from under it."""
    cloud = FakeCloud(create_public_url=[ON],
                      get_public_url=[CloudError("not_found", "none", 404)])
    public, _, runner, _ = make(tmp_path, cloud)
    held = []
    public._spawn = held.append
    public.enable("blog")
    runner.up = True
    (tmp_path / "tunnel.token").write_text("stale")

    public.reconcile()

    assert runner.up and (tmp_path / "tunnel.token").exists()
    assert public.status("blog") == {"state": "enabling"}


def test_signing_out_releases_then_forgets_every_public_url(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], release_public_url=[None], logout=[None])
    public, _, runner, _ = make(tmp_path, cloud)
    public._account.on_forget = public.forget_local
    public.enable("blog")

    public.release_all()
    public._account.sign_out()

    assert cloud.names() == ["create_public_url", "release_public_url", "logout"]
    assert public.status("blog")["reason"]["code"] == "signed_out"
    assert not runner.up and not (tmp_path / "tunnel.token").exists()


def test_turning_on_again_after_expiry_creates_a_new_url(tmp_path):
    fresh = {**ON, "urls": [{"service": "web", "local_hostname": "blog.d.io",
                             "url": "https://new.example.dev"}],
             "expires_at": "2026-09-24T17:00:00Z"}
    cloud = FakeCloud(create_public_url=[ON, fresh])
    public, _, runner, clock = make(tmp_path, cloud)
    public.enable("blog")
    clock.now = EXPIRES + 1  # no reconcile pass has ended the row yet

    public.enable("blog")

    assert cloud.names() == ["create_public_url", "create_public_url"]
    status = public.status("blog")
    assert status["state"] == "on"
    assert status["urls"][0]["url"] == "https://new.example.dev"
    assert runner.up


def test_the_token_directory_is_created_when_missing(tmp_path):
    token = tmp_path / "tunnel" / "token"
    write_token(token, "tun-1")

    assert token.read_text() == "tun-1"


def test_a_naive_expiry_timestamp_is_read_as_utc(tmp_path):
    naive = {**ON, "expires_at": "2026-09-24T15:00:00"}
    public, _, _, _ = make(tmp_path, FakeCloud(create_public_url=[naive]))

    public.enable("blog")

    assert public.status("blog")["expires_at"] == EXPIRES


def test_a_url_ended_for_a_missing_token_is_released_on_the_service(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], get_public_url=[ON], release_public_url=[None])
    public, _, runner, _ = make(tmp_path, cloud)
    public.enable("blog")
    runner.up = False
    (tmp_path / "tunnel.token").unlink()

    public.reconcile()

    assert public.status("blog")["note"]["code"] == "client_failed"
    assert cloud.names()[-1] == "release_public_url"


def test_turning_off_a_failed_url_clears_it(tmp_path):
    cloud = FakeCloud(create_public_url=[CloudError("public_url_active", "x", 409)])
    public, _, _, _ = make(tmp_path, cloud)
    public.enable("blog")

    assert public.disable("blog") == {"state": "off", "note": None}


TWO_HOSTS = [
    {"service": "web", "hostname": "blog.d.io", "local_url": "http://blog.d.io:39080"},
    {"service": "api_v2", "hostname": "api.blog.d.io",
     "local_url": "http://api.blog.d.io:39080"},
]


def test_each_public_url_is_matched_to_its_route_by_local_hostname(tmp_path):
    # Reply order differs from request order on purpose: the key is the
    # hostname, never the position or the service name.
    reply = {**ON, "urls": [
        {"service": "api_v2", "local_hostname": "api.blog.d.io",
         "url": "https://api-v2--k3x9.example.dev"},
        {"service": "web", "local_hostname": "blog.d.io",
         "url": "https://k3x9.example.dev"}]}
    cloud = FakeCloud(create_public_url=[reply])
    public, _, _, _ = make(tmp_path, cloud, hosts=TWO_HOSTS)

    public.enable("blog")

    assert cloud.calls[0][3] == [
        {"local_hostname": "blog.d.io", "service": "web"},
        {"local_hostname": "api.blog.d.io", "service": "api_v2"}]
    assert public.status("blog")["urls"] == [
        {"url": "https://api-v2--k3x9.example.dev", "service": "api_v2",
         "local_url": "http://api.blog.d.io:39080"},
        {"url": "https://k3x9.example.dev", "service": "web",
         "local_url": "http://blog.d.io:39080"}]


def test_a_url_with_no_expiry_stays_on_through_reconcile(tmp_path):
    cloud = FakeCloud(create_public_url=[{**ON, "expires_at": None}],
                      get_public_url=[{**ON, "expires_at": None}])
    public, _, runner, clock = make(tmp_path, cloud)
    public.enable("blog")
    clock.now = EXPIRES + 10**6

    public.reconcile()

    status = public.status("blog")
    assert (status["state"], status["expires_at"]) == ("on", None)
    assert runner.up


def test_the_same_token_again_leaves_the_running_client_alone(tmp_path):
    cloud = FakeCloud(create_public_url=[ON])
    public, _, runner, _ = make(tmp_path, cloud)
    write_token(tmp_path / "tunnel.token", "tun-1")

    public.enable("blog")

    ups = [argv for argv in runner.calls if "up" in argv]
    assert ups and not any("--force-recreate" in argv for argv in ups)


def test_a_new_token_recreates_the_client(tmp_path):
    cloud = FakeCloud(create_public_url=[ON])
    public, _, runner, _ = make(tmp_path, cloud)
    write_token(tmp_path / "tunnel.token", "tun-old")
    runner.up = True

    public.enable("blog")

    assert (tmp_path / "tunnel.token").read_text() == "tun-1"
    assert any("--force-recreate" in argv for argv in runner.calls if "up" in argv)


def test_a_tunnel_provider_failure_reads_in_plain_words(tmp_path):
    cloud = FakeCloud(create_public_url=[CloudError("tunnel_provider_error", "x", 502)])
    public, _, _, _ = make(tmp_path, cloud)

    public.enable("blog")

    assert public.status("blog")["reason"] == {
        "code": "tunnel_provider_error", "message": MESSAGES["tunnel_provider_error"]}


def test_an_active_session_reply_without_credentials_keeps_the_token_held(tmp_path):
    # Releasing here would end the device's live session on the service.
    cloud = FakeCloud(create_public_url=[{**ON, "credentials": None}])
    public, _, runner, _ = make(tmp_path, cloud)
    write_token(tmp_path / "tunnel.token", "tun-1")

    public.enable("blog")

    assert public.status("blog")["state"] == "on"
    assert cloud.names() == ["create_public_url"]
    assert runner.up


def test_a_reply_without_credentials_and_no_token_held_is_released(tmp_path):
    cloud = FakeCloud(create_public_url=[{**ON, "credentials": None}],
                      release_public_url=[None])
    public, _, _, _ = make(tmp_path, cloud)

    public.enable("blog")

    assert public.status("blog")["reason"]["code"] == "client_failed"
    assert cloud.names() == ["create_public_url", "release_public_url"]


def test_an_unchanged_token_is_narrowed_back_to_0640(tmp_path):
    token = tmp_path / "tunnel.token"
    write_token(token, "tun-1")
    os.chmod(token, 0o660)  # the installer's group-write sweep

    assert write_token(token, "tun-1") is False
    assert stat.S_IMODE(os.stat(token).st_mode) == 0o640


def test_after_a_restart_a_url_the_service_still_has_is_picked_up(tmp_path):
    cloud = FakeCloud(get_public_url=[ON])
    public, _, runner, _ = make(tmp_path, cloud)
    write_token(tmp_path / "tunnel.token", "tun-1")

    public.reconcile()

    assert public.status("blog")["urls"][0]["url"] == "https://k3x9.example.dev"
    assert runner.up


def test_a_service_that_cannot_be_reached_leaves_the_client_running(tmp_path):
    cloud = FakeCloud(create_public_url=[ON], get_public_url=[CloudUnavailable("down")])
    public, _, runner, _ = make(tmp_path, cloud)
    public.enable("blog")

    public.reconcile()

    assert public.status("blog")["state"] == "on"
    assert runner.up


def test_a_404_read_before_a_fresh_turn_on_does_not_drop_it(tmp_path):
    fresh = {**ON, "urls": [{"service": "web", "local_hostname": "blog.d.io",
                             "url": "https://new.example.dev"}]}
    public, _, _, clock = make(tmp_path, FakeCloud(create_public_url=[ON]))
    public.enable("blog")
    clock.now = EXPIRES + 1

    class Racing(FakeCloud):
        def get_public_url(self, token, cloud_id):
            public._cloud = FakeCloud(create_public_url=[{**fresh, "expires_at": None}])
            public.enable("blog")
            raise CloudError("not_found", "none", 404)

    public._cloud = Racing()

    public.reconcile()

    assert public.status("blog")["urls"][0]["url"] == "https://new.example.dev"
