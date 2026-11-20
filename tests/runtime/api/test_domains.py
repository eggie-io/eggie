import pytest

from eggie_api.config import ApiConfig
from eggie_api.domain.edge import Domain
from eggie_api.errors import Conflict, NotFound, Unavailable
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from eggie_api.services.account import Account
from eggie_api.services.domains import (CHALLENGE_WINDOW, PAGE_FRESH, SYNC_IDLE,
                                      SYNC_PENDING, Domains)
from eggie_api.services.loader import ProjectLoader
from tests.runtime.api.fake_cloud import FakeCloud

TOKEN = "at"
CHALLENGE = "a" * 43
WEB = "services:\n  web:\n    image: nginx\n    ports: ['8080:80']\n"
DNS = [{"type": "A", "name": "@", "value": "198.51.100.7"}]
PROBLEM = {"code": "dns_elsewhere", "message": "example.com points to another server.",
           "next_step": "Change the A record to 198.51.100.7.",
           "seen": ["203.0.113.9"], "expected": ["198.51.100.7"]}


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def wire(hostname="example.com", project="shop", *, primary=True, status="waiting_dns",
         problem=None, certificate_id=None):
    return {"hostname": hostname, "display": hostname, "project": project,
            "primary": primary, "status": status, "problem": problem, "dns": DNS,
            "certificate_id": certificate_id, "checked_at": "2026-11-20T10:00:00Z",
            "next_check_at": None}


def make(tmp_path, cloud, projects=("shop",)):
    config = ApiConfig(projects_root=tmp_path / "projects", site_host="foo.eggie.io",
                       domain="foo.eggie.space")
    repos = Repos.open(Database(tmp_path / "state.db"))
    for pid in projects:
        folder = config.projects_root / pid
        folder.mkdir(parents=True)
        (folder / "docker-compose.yml").write_text(WEB)
        repos.projects.add(pid, str(folder), config.domain)
    repos.account.update(access_token=TOKEN, refresh_token="rt",
                         access_expires_at=10**12, org_id="org-1")
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    installed: set[str] = set()
    changes: list[int] = []
    clock = Clock()
    domains = Domains(repos.domains, ProjectLoader(config, repos.projects), account, cloud,
                      config, installed=lambda: installed,
                      changed=lambda: changes.append(1), clock=clock)
    return domains, installed, changes, clock


def served(out):
    return {a["hostname"]: a["serving"] for a in out["addresses"]}


def test_add_sends_the_projects_eggie_host_as_target_and_stores_the_answer(tmp_path):
    cloud = FakeCloud(add_instance_domain=[{"domain": wire(), "suggested": "www.example.com"}])
    domains, _, changes, _ = make(tmp_path, cloud)
    out = domains.add("shop", "https://Example.com/")
    assert cloud.calls[-1] == ("add_instance_domain", TOKEN, "example.com", "shop",
                               "shop.foo.eggie.space")
    assert domains.rows() == [Domain("example.com", "shop", True, "waiting_dns", None)]
    assert out["suggested"] == "www.example.com"
    assert [a["hostname"] for a in out["addresses"]] == ["example.com", "shop.foo.eggie.space"]
    assert out["addresses"][0]["dns"] == DNS
    assert changes == [1]


def test_add_passes_the_services_refusal_through(tmp_path):
    cloud = FakeCloud(add_instance_domain=[
        CloudError("domain_taken", "example.com is already attached to blog.", 409)])
    domains, _, changes, _ = make(tmp_path, cloud)
    with pytest.raises(Conflict) as e:
        domains.add("shop", "example.com")
    assert (e.value.code, e.value.message) == ("domain_taken",
                                               "example.com is already attached to blog.")
    assert domains.rows() == [] and changes == []


def test_a_service_without_the_endpoint_reads_as_domains_unavailable(tmp_path):
    cloud = FakeCloud(add_instance_domain=[CloudError("http_404", "Not Found", 404)])
    domains, _, _, _ = make(tmp_path, cloud)
    with pytest.raises(Unavailable) as e:
        domains.add("shop", "example.com")
    assert e.value.code == "domains_unavailable"


def test_addresses_marks_the_eggie_row_redirecting_while_the_primary_serves(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [
        wire("www.example.com", primary=False, status="active", certificate_id="c2"),
        wire("example.com", status="active", certificate_id="c1")]}])
    domains, installed, _, _ = make(tmp_path, cloud)
    installed.update({"c1", "c2"})
    domains.sync()
    out = domains.addresses("shop")
    assert [a["hostname"] for a in out["addresses"]] == [
        "example.com", "www.example.com", "shop.foo.eggie.space"]
    assert served(out) == {"example.com": "direct", "www.example.com": "redirects",
                           "shop.foo.eggie.space": "redirects"}
    assert out["addresses"][-1] == {"kind": "eggie", "hostname": "shop.foo.eggie.space",
                                    "url": "https://shop.foo.eggie.space",
                                    "serving": "redirects"}


def test_addresses_serves_directly_when_the_primary_has_a_problem(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [
        wire("example.com", status="problem", problem=PROBLEM),
        wire("www.example.com", primary=False, status="active", certificate_id="c2")]}])
    domains, installed, _, _ = make(tmp_path, cloud)
    installed.add("c2")
    domains.sync()
    out = domains.addresses("shop")
    assert served(out) == {"example.com": "none", "www.example.com": "direct",
                           "shop.foo.eggie.space": "direct"}
    assert out["addresses"][0]["problem"] == PROBLEM


def test_an_open_page_refreshes_a_pending_mirror_at_most_every_ten_seconds(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [wire()]},
                                        {"domains": [wire(status="issuing")]}])
    domains, _, _, clock = make(tmp_path, cloud)
    domains.sync()
    clock.now += PAGE_FRESH - 1
    assert domains.addresses("shop")["addresses"][0]["status"] == "waiting_dns"
    clock.now += 1
    assert domains.addresses("shop")["addresses"][0]["status"] == "issuing"
    assert cloud.names().count("instance_domains") == 2


def test_sync_keeps_a_domain_whose_project_is_not_here_and_deletes_nothing(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [wire(project="gone")]}])
    domains, _, changes, _ = make(tmp_path, cloud)
    domains.sync()
    assert [d.project_id for d in domains.rows()] == ["gone"]
    assert "delete_instance_domain" not in cloud.names()
    assert changes == [1]


def test_forgetting_a_project_drops_rows_now_and_deletes_on_the_service_later(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [wire()]}] + [{"domains": []}] * 2,
                      delete_instance_domain=[CloudUnavailable("down"), None])
    domains, _, changes, clock = make(tmp_path, cloud)
    domains.sync()
    domains.forget_project("shop")
    assert domains.rows() == [] and changes == [1, 1]
    for _ in range(3):
        clock.now += SYNC_IDLE
        domains.sync()
    assert [c for c in cloud.calls if c[0] == "delete_instance_domain"] == [
        ("delete_instance_domain", TOKEN, "example.com")] * 2, "retried once, then done"


def test_a_queued_domain_the_service_still_lists_stays_out_of_the_mirror(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [wire()]}, {"domains": [wire()]}],
                      delete_instance_domain=[CloudError("http_500", "oops", 500)])
    domains, _, _, clock = make(tmp_path, cloud)
    domains.sync()
    domains.forget_project("shop")
    clock.now += SYNC_IDLE
    domains.sync()
    assert domains.rows() == []


def test_sync_is_every_pass_while_pending_and_every_ten_minutes_once_active(tmp_path):
    active = {"domains": [wire(status="active", certificate_id="c1")]}
    cloud = FakeCloud(instance_domains=[{"domains": [wire()]}, active, active])
    domains, _, _, clock = make(tmp_path, cloud)

    def listed():
        return cloud.names().count("instance_domains")

    domains.sync()
    clock.now += SYNC_PENDING - 1
    domains.sync()
    assert listed() == 1
    clock.now += 1
    domains.sync()
    assert listed() == 2
    clock.now += SYNC_PENDING
    domains.sync()
    assert listed() == 2, "an all-active mirror waits for the idle interval"
    clock.now += SYNC_IDLE - SYNC_PENDING
    domains.sync()
    assert listed() == 3


def test_remove_of_another_projects_domain_is_not_found(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [wire(project="blog")]}])
    domains, _, _, _ = make(tmp_path, cloud, projects=("shop", "blog"))
    domains.sync()
    with pytest.raises(NotFound) as e:
        domains.remove("shop", "example.com")
    assert e.value.code == "domain_not_found"
    assert "delete_instance_domain" not in cloud.names()
    assert [d.hostname for d in domains.rows()] == ["example.com"]


def test_remove_deletes_on_the_service_then_drops_the_row(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [wire()]}],
                      delete_instance_domain=[CloudUnavailable("down"), None])
    domains, _, _, _ = make(tmp_path, cloud)
    domains.sync()
    with pytest.raises(Unavailable):
        domains.remove("shop", "example.com")
    assert [d.hostname for d in domains.rows()] == ["example.com"]
    out = domains.remove("shop", "example.com")
    assert domains.rows() == []
    assert [a["kind"] for a in out["addresses"]] == ["eggie"]


def test_make_primary_stores_the_projects_list_from_the_service(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [
        wire(), wire("www.example.com", primary=False)]}],
        primary_instance_domain=[{"domains": [
            wire("www.example.com"), wire(primary=False)]}])
    domains, _, _, _ = make(tmp_path, cloud)
    domains.sync()
    domains.make_primary("shop", "www.example.com")
    assert [(s["hostname"], s["primary"]) for s in domains.summary("shop")] == [
        ("www.example.com", True), ("example.com", False)]


def test_primary_url_is_none_until_the_primary_serves(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [
        wire(status="active", certificate_id="c1")]}])
    domains, installed, _, _ = make(tmp_path, cloud)
    domains.sync()
    assert domains.primary_url("shop") is None
    installed.add("c1")
    assert domains.primary_url("shop") == "https://example.com"


def test_an_unreachable_service_leaves_the_mirror_and_reports_cloud_unavailable(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [wire()]}, CloudUnavailable("down")],
                      check_instance_domain=[CloudUnavailable("down")])
    domains, _, changes, clock = make(tmp_path, cloud)
    domains.sync()
    clock.now += SYNC_PENDING
    domains.sync()
    assert [d.hostname for d in domains.rows()] == ["example.com"]
    assert changes == [1]
    with pytest.raises(Unavailable) as e:
        domains.check("shop", "example.com")
    assert e.value.code == "cloud_unavailable"


def test_a_challenge_the_service_does_not_know_is_not_found(tmp_path):
    cloud = FakeCloud(acme_challenge=[{"key_authorization": "tok.thumb"},
                                      CloudError("challenge_not_found", "no", 404)])
    domains, _, _, _ = make(tmp_path, cloud)
    assert domains.challenge(CHALLENGE) == "tok.thumb"
    with pytest.raises(NotFound) as e:
        domains.challenge("b" * 43)
    assert e.value.code == "challenge_not_found"


def test_a_failing_domain_sync_still_refreshes_certificates_in_the_same_pass(tmp_path):
    from tests.runtime.api.conftest import _make_env
    cloud = FakeCloud(instance_domains=[RuntimeError("boom")],
                      instance_certificates=[{"certificates": []}],
                      report_instance_projects=[None])
    for env in _make_env(tmp_path, cloud=cloud, site_host="foo.eggie.io",
                         domain="foo.eggie.space"):
        env.repos.account.update(access_token="at", refresh_token="rt",
                                 access_expires_at=10**12, org_id="org-1")
        env.app.state.services.sync.run_once()
    assert cloud.names().index("instance_domains") < cloud.names().index(
        "instance_certificates")


class RacingCloud(FakeCloud):
    """Runs `during_list` once, after the list was fetched and before it is returned."""

    during_list = None

    def instance_domains(self, token):
        out = super().instance_domains(token)
        hook, self.during_list = self.during_list, None
        if hook:
            hook()
        return out


def test_a_refresh_fetched_before_a_remove_does_not_bring_the_domain_back(tmp_path):
    listed = {"domains": [wire(status="active", certificate_id="c1")]}
    cloud = RacingCloud(instance_domains=[listed, listed], delete_instance_domain=[None])
    domains, _, _, clock = make(tmp_path, cloud)
    domains.sync()
    cloud.during_list = lambda: domains.remove("shop", "example.com")
    clock.now += SYNC_IDLE
    domains.sync()
    assert domains.rows() == []


def test_removing_the_primary_lists_again_on_the_next_pass(tmp_path):
    cloud = FakeCloud(instance_domains=[
        {"domains": [wire(status="active", certificate_id="c1"),
                     wire("www.example.com", primary=False, status="active",
                          certificate_id="c2")]},
        {"domains": [wire("www.example.com", status="active", certificate_id="c2")]}],
        delete_instance_domain=[None])
    domains, _, _, clock = make(tmp_path, cloud)
    domains.sync()
    domains.remove("shop", "example.com")
    clock.now += 1
    domains.sync()
    assert cloud.names().count("instance_domains") == 2
    assert [(s["hostname"], s["primary"]) for s in domains.summary("shop")] == [
        ("www.example.com", True)]


def test_a_domain_the_service_does_not_know_lists_again_on_the_next_pass(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [wire(status="active",
                                                          certificate_id="c1")]},
                                        {"domains": []}],
                      check_instance_domain=[CloudError("domain_not_found", "gone", 404)])
    domains, _, _, clock = make(tmp_path, cloud)
    domains.sync()
    with pytest.raises(NotFound):
        domains.check("shop", "example.com")
    clock.now += 1
    domains.sync()
    assert domains.rows() == []


def test_a_project_without_a_web_service_has_no_primary_url(tmp_path):
    cloud = FakeCloud(instance_domains=[{"domains": [
        wire(status="active", certificate_id="c1")]}])
    domains, installed, _, _ = make(tmp_path, cloud)
    installed.add("c1")
    domains.sync()
    (tmp_path / "projects" / "shop" / "docker-compose.yml").write_text("services: {}\n")
    assert domains.primary_url("shop") is None


def refused(domains, **client):
    with pytest.raises(NotFound) as e:
        domains.challenge(CHALLENGE, **client)
    return e.value.code == "challenge_not_found"


def test_a_malformed_challenge_token_never_reaches_the_service(tmp_path):
    cloud = FakeCloud()
    domains, _, _, _ = make(tmp_path, cloud)
    for bad in ("too-short", "a" * 201, "a" * 42 + ".", "a" * 42 + "/", ""):
        with pytest.raises(NotFound):
            domains.challenge(bad, peer="203.0.113.9")
    assert cloud.calls == []


def test_one_address_gets_twenty_challenges_a_minute_and_others_still_get_theirs(tmp_path):
    cloud = FakeCloud(acme_challenge=[{"key_authorization": "k.t"}] * 21)
    domains, _, _, _ = make(tmp_path, cloud)
    for _ in range(20):
        assert domains.challenge(CHALLENGE, forwarded_for="203.0.113.9, 10.0.0.2") == "k.t"
    assert refused(domains, forwarded_for="203.0.113.9")
    assert domains.challenge(CHALLENGE, peer="198.51.100.1") == "k.t"
    assert cloud.names().count("acme_challenge") == 21


def test_a_refused_address_is_answered_again_once_its_minute_has_passed(tmp_path):
    cloud = FakeCloud(acme_challenge=[{"key_authorization": "k.t"}] * 21)
    domains, _, _, clock = make(tmp_path, cloud)
    for _ in range(20):
        domains.challenge(CHALLENGE, peer="203.0.113.9")
    assert refused(domains, peer="203.0.113.9")
    clock.now += CHALLENGE_WINDOW
    assert domains.challenge(CHALLENGE, peer="203.0.113.9") == "k.t"


def test_the_three_hundred_and_first_challenge_in_a_minute_is_refused(tmp_path):
    cloud = FakeCloud(acme_challenge=[{"key_authorization": "k.t"}] * 300)
    domains, _, _, _ = make(tmp_path, cloud)
    for n in range(300):
        domains.challenge(CHALLENGE, forwarded_for=f"203.0.113.{n % 20}")
    assert refused(domains, forwarded_for="198.51.100.1")
    assert cloud.names().count("acme_challenge") == 300


def test_the_summary_of_a_project_with_no_row_is_empty_not_an_error(tmp_path):
    # A project deleted mid-listing must not take the listing down.
    domains, _, _, _ = make(tmp_path, FakeCloud())
    assert domains.summary("gone") == []


class NoLoad(ProjectLoader):
    def load(self, project_id):
        raise AssertionError(f"{project_id} was loaded")


def test_primary_url_of_a_project_without_domains_loads_nothing(tmp_path):
    config = ApiConfig(projects_root=tmp_path / "projects", site_host="foo.eggie.io",
                       domain="foo.eggie.space")
    repos = Repos.open(Database(tmp_path / "state.db"))
    repos.projects.add("shop", str(tmp_path / "projects" / "shop"), config.domain)
    domains = Domains(repos.domains, NoLoad(config, repos.projects), None, FakeCloud(),
                      config, installed=lambda: set(), changed=lambda: None)
    assert domains.primary_url("shop") is None
