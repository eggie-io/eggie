import json
import stat

import pytest

from eggie_api.domain.edge import Pair
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from eggie_api.services.account import Account
from eggie_api.services.certificates import CHECK_EVERY, Certificates
from tests.runtime.api.fake_cloud import FakeCloud


def entry(cid, kind, names=None):
    return {"id": cid, "kind": kind, "names": names or [f"{cid}.example.com"],
            "not_after": "2027-01-11T10:00:00Z"}


def pair(cid, kind, names=None):
    return {**entry(cid, kind, names), "certificate": f"CERT-{cid}",
            "private_key": f"KEY-{cid}"}


BOTH = {"certificates": [entry("i1", "instance"), entry("d1", "domain")]}
ONLY_I1 = {"certificates": [entry("i1", "instance")]}


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def account(tmp_path):
    repos = Repos.open(Database(tmp_path / "state.db"))
    repos.account.update(access_token="at", refresh_token="rt",
                         access_expires_at=10**12, org_id="org-1")
    return Account(repos.account, repos.cloud_projects, FakeCloud(), spawn=lambda fn: None)


@pytest.fixture
def certs_dir(tmp_path):
    return tmp_path / "edge" / "certs"


def make(tmp_path, account, cloud, clock):
    return Certificates(tmp_path / "edge", account, cloud, clock=clock)


@pytest.fixture
def cloud():
    return FakeCloud(
        instance_certificates=[BOTH, ONLY_I1, ONLY_I1, ONLY_I1],
        instance_certificate=[pair("i1", "instance"), pair("d1", "domain")] * 3)


@pytest.fixture
def certs(tmp_path, account, cloud, clock):
    return make(tmp_path, account, cloud, clock)


def test_first_pass_installs_every_listed_certificate(certs, cloud, certs_dir):
    certs.refresh()
    assert {p.id: p.kind for p in certs.installed()} == {"i1": "instance", "d1": "domain"}
    assert stat.S_IMODE((certs_dir / "d1" / "key.pem").stat().st_mode) == 0o600
    assert stat.S_IMODE((certs_dir / "d1" / "cert.pem").stat().st_mode) == 0o644
    assert (certs_dir / "d1" / "key.pem").read_text() == "KEY-d1"
    meta = json.loads((certs_dir / "d1" / "meta.json").read_text())
    assert meta == entry("d1", "domain")
    assert not list(certs_dir.glob("*.tmp"))
    assert certs.installed()[0] == Pair("d1", "domain", str(certs_dir / "d1" / "cert.pem"),
                                        str(certs_dir / "d1" / "key.pem"), ("d1.example.com",))


def test_a_second_pass_within_a_day_asks_nothing(certs, cloud, clock):
    certs.refresh()
    clock.now += CHECK_EVERY - 1
    certs.refresh()
    assert cloud.names().count("instance_certificates") == 1
    assert cloud.names().count("instance_certificate") == 2


def test_a_wanted_id_not_on_disk_lists_again_at_once(certs, cloud):
    certs.refresh()
    certs.refresh({"i1", "d1"})
    assert cloud.names().count("instance_certificates") == 1
    certs.refresh({"d2"})
    assert cloud.names().count("instance_certificates") == 2


def test_a_list_is_asked_again_after_a_day(certs, cloud, clock):
    certs.refresh()
    clock.now += CHECK_EVERY
    certs.refresh()
    assert cloud.names().count("instance_certificates") == 2


INSTANCE_NAMES = ["foo.eggie.io", "*.foo.eggie.space"]
FIRST = {"certificates": [entry("i1", "instance", INSTANCE_NAMES),
                          entry("d1", "domain", ["example.com"])]}
RENEWED = {"certificates": [entry("i2", "instance", INSTANCE_NAMES),
                            entry("d2", "domain", ["example.com"])]}


def renewal(tmp_path, account, clock, *, later):
    """i1 and d1 installed, then renewed as i2 and d2 whose next two passes of
    downloads fail."""
    down = CloudUnavailable("down")
    cloud = FakeCloud(instance_certificates=[FIRST, RENEWED],
                      instance_certificate=[pair("i1", "instance", INSTANCE_NAMES),
                                            pair("d1", "domain", ["example.com"]),
                                            down, down, down, down, *later])
    certs = make(tmp_path, account, cloud, clock)
    certs.refresh()
    clock.now += CHECK_EVERY
    certs.refresh()
    return certs


def test_a_renewed_pair_that_cannot_be_fetched_leaves_the_held_pair_serving(
        tmp_path, account, clock, certs_dir):
    certs = renewal(tmp_path, account, clock, later=[])
    assert [p.id for p in certs.installed()] == ["d1", "i1"]
    certs.refresh()
    assert (certs_dir / "i1").is_dir() and (certs_dir / "d1").is_dir()
    assert [p.id for p in certs.installed()] == ["d1", "i1"]


def test_once_the_renewed_pair_is_installed_the_held_one_stops_at_once_and_goes_next_pass(
        tmp_path, account, clock, certs_dir):
    certs = renewal(tmp_path, account, clock, later=[
        pair("i2", "instance", INSTANCE_NAMES), pair("d2", "domain", ["example.com"])])
    certs.refresh()
    assert [p.id for p in certs.installed()] == ["d1", "i1"]
    certs.refresh()
    assert [p.id for p in certs.installed()] == ["d2", "i2"]
    assert (certs_dir / "d1").is_dir()
    certs.refresh()
    assert sorted(e.name for e in certs_dir.iterdir()) == ["d2", "i2"]


def test_only_the_renewed_pair_covers_its_name_once_installed(tmp_path, account, clock):
    certs = renewal(tmp_path, account, clock, later=[
        CloudUnavailable("down"), pair("d2", "domain", ["example.com"])])
    certs.refresh()
    certs.refresh()
    assert [(p.id, p.names) for p in certs.installed()] == [
        ("d2", ("example.com",)), ("i1", tuple(INSTANCE_NAMES))]


def test_a_wanted_id_held_on_disk_does_not_list_every_pass(tmp_path, account, clock):
    certs = renewal(tmp_path, account, clock, later=[CloudUnavailable("down")] * 4)
    for _ in range(2):
        certs.refresh({"d1"})
    assert certs._cloud.names().count("instance_certificates") == 2


def test_a_pair_no_listed_certificate_shares_a_name_with_stops_now_and_leaves_disk_next_pass(
        certs, cloud, clock, certs_dir):
    certs.refresh()
    clock.now += CHECK_EVERY
    certs.refresh()
    assert [p.id for p in certs.installed()] == ["i1"] and (certs_dir / "d1").exists()
    certs.refresh()
    assert not (certs_dir / "d1").exists()
    assert [p.id for p in certs.installed()] == ["i1"]


def test_a_pair_that_fails_to_download_does_not_stop_the_others(
        tmp_path, account, clock):
    cloud = FakeCloud(instance_certificates=[BOTH],
                      instance_certificate=[CloudUnavailable("x"), pair("d1", "domain")])
    certs = make(tmp_path, account, cloud, clock)
    certs.refresh()
    assert [p.id for p in certs.installed()] == ["d1"]


def test_a_failed_pair_is_fetched_again_on_the_next_pass_without_a_new_list(
        tmp_path, account, clock):
    cloud = FakeCloud(
        instance_certificates=[BOTH],
        instance_certificate=[CloudError("no_certificate", "n", 404),
                              pair("d1", "domain"), pair("i1", "instance")])
    certs = make(tmp_path, account, cloud, clock)
    certs.refresh()
    certs.refresh()
    assert [p.id for p in certs.installed()] == ["d1", "i1"]
    assert cloud.names().count("instance_certificates") == 1


def test_an_incomplete_pair_is_not_installed(tmp_path, account, clock, certs_dir):
    broken = {k: v for k, v in pair("i1", "instance").items() if k != "certificate"}
    cloud = FakeCloud(instance_certificates=[ONLY_I1], instance_certificate=[broken])
    certs = make(tmp_path, account, cloud, clock)
    certs.refresh()
    assert certs.installed() == []
    assert not list(certs_dir.glob("*.tmp"))


def test_an_id_that_would_leave_the_certs_directory_is_refused(tmp_path, account, clock, certs_dir):
    evil = {"certificates": [entry("../x", "domain")]}
    cloud = FakeCloud(instance_certificates=[evil],
                      instance_certificate=[pair("../x", "domain")])
    certs = make(tmp_path, account, cloud, clock)
    certs.refresh()
    assert not (tmp_path / "edge" / "x").exists()
    assert certs.installed() == []


def test_a_directory_without_meta_json_is_not_installed(certs, certs_dir):
    (certs_dir / "x").mkdir(parents=True)
    (certs_dir / "x" / "cert.pem").write_text("c")
    (certs_dir / "x" / "key.pem").write_text("k")
    assert certs.installed() == []


def test_a_directory_without_meta_json_is_fetched_over(tmp_path, account, clock, certs_dir):
    cloud = FakeCloud(instance_certificates=[ONLY_I1],
                      instance_certificate=[pair("i1", "instance")])
    certs = make(tmp_path, account, cloud, clock)
    (certs_dir / "i1").mkdir(parents=True)
    (certs_dir / "i1" / "cert.pem").write_text("half")
    certs.refresh()
    assert [p.id for p in certs.installed()] == ["i1"]
    assert (certs_dir / "i1" / "cert.pem").read_text() == "CERT-i1"


def test_after_a_restart_everything_on_disk_counts_until_the_service_answers(
        certs, tmp_path, account, clock):
    certs.refresh()
    down = FakeCloud(instance_certificates=[CloudUnavailable("down")])
    fresh = make(tmp_path, account, down, clock)
    assert [p.id for p in fresh.installed()] == ["d1", "i1"]
    fresh.refresh()
    assert [p.id for p in fresh.installed()] == ["d1", "i1"]


def test_a_failed_list_changes_nothing_and_is_retried_next_pass(
        tmp_path, account, clock):
    cloud = FakeCloud(instance_certificates=[CloudUnavailable("down"), ONLY_I1],
                      instance_certificate=[pair("i1", "instance")])
    certs = make(tmp_path, account, cloud, clock)
    certs.refresh()
    assert certs.installed() == []
    certs.refresh()
    assert [p.id for p in certs.installed()] == ["i1"]


def test_signed_out_asks_nothing(tmp_path, clock):
    repos = Repos.open(Database(tmp_path / "state.db"))
    cloud = FakeCloud()
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    make(tmp_path, account, cloud, clock).refresh()
    assert cloud.calls == []
