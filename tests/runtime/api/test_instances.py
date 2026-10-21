import pytest
from fastapi.testclient import TestClient

from eggie_api.errors import Conflict, Unavailable, Upstream
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from eggie_api.services.account import Account
from eggie_api.services.instances import Instances
from tests.runtime.api.fake_cloud import FakeCloud
from tests.runtime.api.test_api_public import console

ROW = {
    "id": "i1", "name": "foo", "display_name": "Foo", "status": "running", "step": None,
    "last_error": None, "provider": "hetzner", "region_id": "fsn1",
    "region_label": "Falkenstein", "size_id": "cx22", "size_label": "2 vCPU",
    "monthly_price_cents": 500, "login_name": "foo", "console_hostname": "foo.eggie.io",
    "site_hostname": "foo.eggie.space", "ssh_command": "ssh foo@1.2.3.4",
    "ipv4": "1.2.3.4", "signed_out": False, "slow_boot": False,
    "certificate_not_after": None, "renewal_failing": False, "is_owner": True,
    "can_delete": True, "ssh_keys": [], "created_at": "2026-10-01T00:00:00Z",
}
OTHER = {**ROW, "id": "i2", "console_hostname": "bar.eggie.io"}


def instances(tmp_path, cloud, *, site_host=None, signed_in=True) -> Instances:
    repos = Repos.open(Database(tmp_path / "state.db"))
    if signed_in:
        repos.account.update(access_token="at", refresh_token="rt",
                             access_expires_at=10**12, org_id="org-1")
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    return Instances(account, cloud, site_host)


def test_lists_kitchens_in_service_order_with_only_the_fields_the_switcher_needs(tmp_path):
    out = instances(tmp_path, FakeCloud(list_instances=[[ROW, OTHER]])).list()

    kitchen = {"display_name": "Foo", "status": "running", "signed_out": False,
               "is_owner": True, "current": False}
    assert out == {"instances": [
        {**kitchen, "id": "i1", "console_hostname": "foo.eggie.io"},
        {**kitchen, "id": "i2", "console_hostname": "bar.eggie.io"},
    ]}


def test_marks_current_only_the_kitchen_this_server_is(tmp_path):
    out = instances(tmp_path, FakeCloud(list_instances=[[ROW, OTHER]]),
                    site_host="foo.eggie.io").list()

    assert [i["current"] for i in out["instances"]] == [True, False]


def test_drops_rows_without_a_console_hostname_or_id(tmp_path):
    rows = [{**ROW, "console_hostname": ""}, {**ROW, "id": None}, OTHER]

    out = instances(tmp_path, FakeCloud(list_instances=[rows])).list()

    assert [i["id"] for i in out["instances"]] == ["i2"]


def test_an_empty_account_lists_nothing(tmp_path):
    assert instances(tmp_path, FakeCloud(list_instances=[[]])).list() == {"instances": []}


def test_not_signed_in_is_a_409(tmp_path):
    with pytest.raises(Conflict) as e:
        instances(tmp_path, FakeCloud(), signed_in=False).list()
    assert e.value.code == "not_signed_in"


def test_service_down_is_a_503(tmp_path):
    with pytest.raises(Unavailable) as e:
        instances(tmp_path, FakeCloud(list_instances=[CloudUnavailable("down")])).list()
    assert e.value.code == "cloud_unavailable"


def test_a_service_error_is_a_502_with_its_message(tmp_path):
    with pytest.raises(Upstream) as e:
        instances(tmp_path, FakeCloud(list_instances=[CloudError("x", "boom", 500)])).list()
    assert (e.value.code, e.value.message) == ("cloud_error", "boom")


def test_instances_is_reachable_from_the_console_only(env):
    assert console(env.app, env.client).get("/api/instances").status_code != 404
    assert env.client.get("/instances").status_code == 404


def test_own_login_is_the_login_of_the_kitchen_this_server_is(tmp_path):
    rows = [{**OTHER, "login_name": "bar"}, {**ROW, "login_name": "ada"}]
    cloud = FakeCloud(list_instances=[rows])

    assert instances(tmp_path, cloud, site_host="foo.eggie.io").own_login() == "ada"


@pytest.mark.parametrize("cloud, signed_in", [
    (FakeCloud(list_instances=[CloudUnavailable("down")]), True),
    (FakeCloud(list_instances=[CloudError("x", "boom", 500)]), True),
    (FakeCloud(list_instances=[[OTHER]]), True),
    (FakeCloud(), False),
    (FakeCloud(list_instances=[None]), True),
    (FakeCloud(list_instances=[["junk", {**ROW, "login_name": None}]]), True),
], ids=["service-down", "service-error", "not-listed", "signed-out", "empty-body", "malformed-rows"])
def test_own_login_is_none_when_the_service_cannot_say(tmp_path, cloud, signed_in):
    assert instances(tmp_path, cloud, site_host="foo.eggie.io",
                     signed_in=signed_in).own_login() is None


def test_own_login_asks_the_service_once_it_has_an_answer(tmp_path):
    kitchens = instances(tmp_path, FakeCloud(list_instances=[[ROW]]), site_host="foo.eggie.io")

    assert kitchens.own_login() == kitchens.own_login() == "foo"
