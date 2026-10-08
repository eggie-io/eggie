from eggie_api.services.account import Account
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from eggie_api.constants import VERIFY_PROJECT_ID
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from eggie_api.services.sync import Create, Delete, Forget, plan, run_pass
from tests.runtime.api.fake_cloud import FakeCloud

M = lambda cloud_id, org="org-1": {"cloud_id": cloud_id, "org_id": org}


def test_plan_creates_unmapped_deletes_orphaned_and_forgets_other_orgs():
    actions = plan({"blog", "shop"},
                   {"shop": M("c-shop"), "old": M("c-old"), "blog": M("c-x", "org-9")},
                   "org-1")
    assert actions == [Forget("blog"), Delete("old", "c-old"), Create("blog")]


def test_plan_leaves_mapped_projects_alone():
    assert plan({"blog"}, {"blog": M("c-1")}, "org-1") == []


def setup(tmp_path, cloud, projects=()):
    repos = Repos.open(Database(tmp_path / "state.db"))
    for pid in projects:
        repos.projects.add(pid, f"/p/{pid}", "d")
    repos.account.update(access_token="at", refresh_token="rt",
                         access_expires_at=10**12, email="a@x", org_id="org-1")
    return repos, Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)


def test_a_pass_creates_records_with_this_devices_client_ref(tmp_path):
    cloud = FakeCloud(create_project=[{"id": "c-1"}])
    repos, account = setup(tmp_path, cloud, ["blog"])

    run_pass(account, cloud, repos)

    assert cloud.calls == [("create_project", "at", "blog", f"{account.device_id}/blog")]
    assert repos.cloud_projects.mapping() == {"blog": M("c-1")}
    assert repos.account.get()["sync_ok_at"] is not None


def test_the_install_smoke_test_project_is_never_sent(tmp_path):
    cloud = FakeCloud()
    repos, account = setup(tmp_path, cloud, [VERIFY_PROJECT_ID])

    run_pass(account, cloud, repos)

    assert cloud.calls == []


def test_a_record_already_gone_on_the_service_counts_as_deleted(tmp_path):
    cloud = FakeCloud(delete_project=[CloudError("not_found", "gone", 404)])
    repos, account = setup(tmp_path, cloud)
    repos.cloud_projects.map("old", "c-old", "org-1")

    run_pass(account, cloud, repos)

    assert repos.cloud_projects.mapping() == {}


def test_a_failed_delete_keeps_the_mapping_and_the_rest_carries_on(tmp_path):
    cloud = FakeCloud(delete_project=[CloudError("method_not_allowed", "no", 405)],
                      create_project=[{"id": "c-1"}])
    repos, account = setup(tmp_path, cloud, ["blog"])
    repos.cloud_projects.map("old", "c-old", "org-1")

    run_pass(account, cloud, repos)

    assert repos.cloud_projects.mapping() == {"old": M("c-old"), "blog": M("c-1")}
    row = repos.account.get()
    assert "old" in row["sync_error"] and row["sync_ok_at"] is None


def test_an_unreachable_service_is_recorded_and_retried_next_pass(tmp_path):
    cloud = FakeCloud(create_project=[CloudUnavailable("down"), {"id": "c-1"}])
    repos, account = setup(tmp_path, cloud, ["blog"])

    run_pass(account, cloud, repos)
    assert repos.cloud_projects.mapping() == {}
    run_pass(account, cloud, repos)
    assert repos.cloud_projects.mapping() == {"blog": M("c-1")}
    assert repos.account.get()["sync_error"] is None


def test_a_pass_finishing_after_sign_out_does_not_write_stale_sync_fields(tmp_path):
    class Wrapped(FakeCloud):
        def create_project(self, token, name, client_ref):
            out = super().create_project(token, name, client_ref)
            account._forget(None)
            return out

    cloud = Wrapped(create_project=[{"id": "c-1"}])
    repos, account = setup(tmp_path, cloud, ["blog"])

    run_pass(account, cloud, repos)

    row = repos.account.get()
    assert row["sync_ok_at"] is None
    assert row["sync_error"] is None
    assert account.signed_in is False


def test_nothing_happens_when_signed_out(tmp_path):
    cloud = FakeCloud()
    repos, account = setup(tmp_path, cloud, ["blog"])
    repos.account.update(access_token=None)

    run_pass(account, cloud, repos)

    assert cloud.calls == []
