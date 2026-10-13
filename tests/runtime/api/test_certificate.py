import os
import stat

from eggie_api.services.account import Account
from eggie_api.services.certificate import CHECK_EVERY, Certificate
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from tests.runtime.api.fake_cloud import FakeCloud

NAMES = ("foo.eggie.io", "foo.eggie.space", "*.foo.eggie.space")


def issued(not_after="2027-01-11T10:00:00Z", **over):
    return {"names": list(NAMES), "certificate": "CERT-" + not_after,
            "private_key": "KEY-" + not_after, "not_after": not_after, **over}


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def make(tmp_path, cloud, *, signed_in=True):
    repos = Repos.open(Database(tmp_path / "state.db"))
    if signed_in:
        repos.account.update(access_token="at", refresh_token="rt",
                             access_expires_at=10**12, org_id="org-1")
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    clock = Clock()
    cert = Certificate(tmp_path / "tls", account, cloud, names=NAMES, clock=clock)
    return cert, clock


def test_a_missing_certificate_is_fetched_and_written_with_a_private_key(tmp_path):
    cloud = FakeCloud(instance_certificate=[issued()])
    cert, _ = make(tmp_path, cloud)
    cert.refresh()
    assert cert.cert_path.read_text() == "CERT-2027-01-11T10:00:00Z"
    assert cert.key_path.read_text() == "KEY-2027-01-11T10:00:00Z"
    assert stat.S_IMODE(os.stat(cert.key_path).st_mode) == 0o600
    assert cert.installed_not_after() == 1799661600.0
    assert not list(cert.cert_path.parent.glob("*.tmp"))


def test_an_installed_certificate_is_rechecked_once_a_day(tmp_path):
    cloud = FakeCloud(instance_certificate=[issued(), issued()])
    cert, clock = make(tmp_path, cloud)
    cert.refresh()
    clock.now += CHECK_EVERY - 1
    cert.refresh()
    assert len(cloud.calls) == 1
    clock.now += 2
    cert.refresh()
    assert len(cloud.calls) == 2


def test_the_same_expiry_leaves_the_files_alone_a_later_one_replaces_them(tmp_path):
    cloud = FakeCloud(instance_certificate=[
        issued(), issued(certificate="CERT-rewritten"), issued("2027-03-01T00:00:00Z")])
    cert, clock = make(tmp_path, cloud)
    cert.refresh()
    clock.now += CHECK_EVERY + 1
    cert.refresh()
    assert cert.cert_path.read_text() == "CERT-2027-01-11T10:00:00Z"
    clock.now += CHECK_EVERY + 1
    cert.refresh()
    assert cert.cert_path.read_text() == "CERT-2027-03-01T00:00:00Z"
    assert cert.key_path.read_text() == "KEY-2027-03-01T00:00:00Z"


def test_a_failed_fetch_is_retried_on_the_next_pass(tmp_path):
    # First boot often runs before the network is up; a day's wait would
    # leave Traefik on its self-signed certificate until tomorrow.
    cloud = FakeCloud(instance_certificate=[CloudUnavailable("down"), issued()])
    cert, _ = make(tmp_path, cloud)
    cert.refresh()
    assert not cert.cert_path.exists()
    cert.refresh()
    assert cert.cert_path.exists()


def test_a_failed_fetch_keeps_the_installed_files(tmp_path):
    cloud = FakeCloud(instance_certificate=[issued(), CloudError("no_certificate", "n", 404)])
    cert, clock = make(tmp_path, cloud)
    cert.refresh()
    clock.now += CHECK_EVERY + 1
    cert.refresh()
    assert cert.cert_path.read_text() == "CERT-2027-01-11T10:00:00Z"


def test_a_certificate_that_misses_a_name_is_refused(tmp_path):
    cloud = FakeCloud(instance_certificate=[issued(names=["foo.eggie.io", "foo.eggie.space"])])
    cert, _ = make(tmp_path, cloud)
    cert.refresh()
    assert not cert.cert_path.exists() and not cert.key_path.exists()


def test_nothing_is_fetched_while_not_signed_in(tmp_path):
    cloud = FakeCloud()
    cert, _ = make(tmp_path, cloud, signed_in=False)
    cert.refresh()
    assert cloud.calls == []
