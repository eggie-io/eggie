from eggie_api.core.account import Account
from eggie_api.core.cloud import CloudError, CloudUnavailable
from eggie_api.core.constants import VERIFY_PROJECT_ID
from eggie_api.core.state import State
from eggie_api.core.sync import Create, Delete, Forget, plan, run_pass
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
    state = State(tmp_path / "state.db")
    for pid in projects:
        state.add_project(pid, f"/p/{pid}", "d")
    state.update_account(access_token="at", refresh_token="rt",
                         access_expires_at=10**12, email="a@x", org_id="org-1")
    return state, Account(state, cloud, spawn=lambda fn: None)


def test_a_pass_creates_records_with_this_devices_client_ref(tmp_path):
    cloud = FakeCloud(create_project=[{"id": "c-1"}])
    state, account = setup(tmp_path, cloud, ["blog"])

    run_pass(account, cloud, state)

    assert cloud.calls == [("create_project", "at", "blog", f"{account.device_id}/blog")]
    assert state.cloud_mapping() == {"blog": M("c-1")}
    assert state.get_account()["sync_ok_at"] is not None


def test_the_install_smoke_test_project_is_never_sent(tmp_path):
    cloud = FakeCloud()
    state, account = setup(tmp_path, cloud, [VERIFY_PROJECT_ID])

    run_pass(account, cloud, state)

    assert cloud.calls == []


def test_a_record_already_gone_on_the_service_counts_as_deleted(tmp_path):
    cloud = FakeCloud(delete_project=[CloudError("not_found", "gone", 404)])
    state, account = setup(tmp_path, cloud)
    state.map_cloud_project("old", "c-old", "org-1")

    run_pass(account, cloud, state)

    assert state.cloud_mapping() == {}


def test_a_failed_delete_keeps_the_mapping_and_the_rest_carries_on(tmp_path):
    cloud = FakeCloud(delete_project=[CloudError("method_not_allowed", "no", 405)],
                      create_project=[{"id": "c-1"}])
    state, account = setup(tmp_path, cloud, ["blog"])
    state.map_cloud_project("old", "c-old", "org-1")

    run_pass(account, cloud, state)

    assert state.cloud_mapping() == {"old": M("c-old"), "blog": M("c-1")}
    row = state.get_account()
    assert "old" in row["sync_error"] and row["sync_ok_at"] is None


def test_an_unreachable_service_is_recorded_and_retried_next_pass(tmp_path):
    cloud = FakeCloud(create_project=[CloudUnavailable("down"), {"id": "c-1"}])
    state, account = setup(tmp_path, cloud, ["blog"])

    run_pass(account, cloud, state)
    assert state.cloud_mapping() == {}
    run_pass(account, cloud, state)
    assert state.cloud_mapping() == {"blog": M("c-1")}
    assert state.get_account()["sync_error"] is None


def test_a_pass_finishing_after_sign_out_does_not_write_stale_sync_fields(tmp_path):
    class Wrapped(FakeCloud):
        def create_project(self, token, name, client_ref):
            out = super().create_project(token, name, client_ref)
            account._forget(None)
            return out

    cloud = Wrapped(create_project=[{"id": "c-1"}])
    state, account = setup(tmp_path, cloud, ["blog"])

    run_pass(account, cloud, state)

    row = state.get_account()
    assert row["sync_ok_at"] is None
    assert row["sync_error"] is None
    assert account.signed_in is False


def test_nothing_happens_when_signed_out(tmp_path):
    cloud = FakeCloud()
    state, account = setup(tmp_path, cloud, ["blog"])
    state.update_account(access_token=None)

    run_pass(account, cloud, state)

    assert cloud.calls == []
