from eggie_api.config import ApiConfig
from eggie_api.constants import VERIFY_PROJECT_ID
from eggie_api.domain.project import STARTED_OK
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from eggie_api.services.account import Account
from eggie_api.services.instance_projects import HEARTBEAT, InstanceProjects
from eggie_api.services.loader import ProjectLoader
from tests.runtime.api.fake_cloud import FakeCloud

WEB = "services:\n  web:\n    image: nginx\n    ports: ['8080:80']\n"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def make(tmp_path, cloud, projects, *, signed_in=True):
    """`projects` maps id -> (status, compose text or None for no folder)."""
    config = ApiConfig(projects_root=tmp_path / "projects", site_host="foo.eggie.io",
                       domain="foo.eggie.space")
    repos = Repos.open(Database(tmp_path / "state.db"))
    for pid, (status, compose) in projects.items():
        folder = config.projects_root / pid
        if compose is not None:
            folder.mkdir(parents=True)
            (folder / "docker-compose.yml").write_text(compose)
        repos.projects.add(pid, str(folder), config.domain, status=status)
    if signed_in:
        repos.account.update(access_token="at", refresh_token="rt",
                             access_expires_at=10**12, org_id="org-1")
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    clock = Clock()
    report = InstanceProjects(repos.projects, ProjectLoader(config, repos.projects),
                              account, cloud, clock=clock)
    return report, repos, clock


def sent(cloud):
    return [call[2] for call in cloud.calls if call[0] == "report_instance_projects"]


def test_a_running_project_is_reported_with_its_primary_url(tmp_path):
    cloud = FakeCloud(report_instance_projects=[None])
    report, _, _ = make(tmp_path, cloud, {"shop": (STARTED_OK, WEB)})
    report.report()
    assert cloud.calls == [("report_instance_projects", "at", [
        {"ref": "shop", "name": "shop", "public_url": "https://shop.foo.eggie.space"}])]


def test_only_a_running_project_that_loads_gets_a_url(tmp_path):
    cloud = FakeCloud(report_instance_projects=[None])
    report, _, _ = make(tmp_path, cloud, {
        "stopped": ("stopped", WEB), "looping": ("crash_looping", WEB),
        "empty": (STARTED_OK, "services: {}\n"), "gone": (STARTED_OK, None),
        "broken": (STARTED_OK, "services: [\n")})
    report.report()
    assert sent(cloud) == [[
        {"ref": ref, "name": ref, "public_url": None}
        for ref in ("broken", "empty", "gone", "looping", "stopped")]]


def test_the_install_smoke_test_project_is_left_out(tmp_path):
    cloud = FakeCloud(report_instance_projects=[None])
    report, _, _ = make(tmp_path, cloud, {VERIFY_PROJECT_ID: (STARTED_OK, WEB)})
    report.report()
    assert sent(cloud) == [[]]


def test_an_unchanged_list_is_resent_only_as_the_heartbeat(tmp_path):
    cloud = FakeCloud(report_instance_projects=[None, None])
    report, _, clock = make(tmp_path, cloud, {"shop": (STARTED_OK, WEB)})
    report.report()
    clock.now += HEARTBEAT - 1
    report.report()
    assert len(sent(cloud)) == 1
    clock.now += 2
    report.report()
    assert len(sent(cloud)) == 2


def test_a_changed_list_is_sent_on_the_next_pass(tmp_path):
    cloud = FakeCloud(report_instance_projects=[None, None])
    report, repos, clock = make(tmp_path, cloud, {"shop": (STARTED_OK, WEB)})
    report.report()
    repos.projects.set_status("shop", "stopped")
    clock.now += 1
    report.report()
    assert sent(cloud)[-1] == [{"ref": "shop", "name": "shop", "public_url": None}]


def test_a_refused_list_is_not_resent_until_it_changes(tmp_path):
    refused = CloudError("invalid_public_url", "bad", 422)
    cloud = FakeCloud(report_instance_projects=[refused, None])
    report, repos, clock = make(tmp_path, cloud, {"shop": (STARTED_OK, WEB)})
    report.report()
    clock.now += HEARTBEAT + 1
    report.report()
    assert len(sent(cloud)) == 1
    repos.projects.set_status("shop", "stopped")
    report.report()
    assert len(sent(cloud)) == 2


def test_a_service_outage_backs_off_then_retries(tmp_path):
    cloud = FakeCloud(report_instance_projects=[
        CloudUnavailable("down"), CloudError("http_500", "oops", 500), None])
    report, _, clock = make(tmp_path, cloud, {"shop": (STARTED_OK, WEB)})
    report.report()
    report.report()
    assert len(sent(cloud)) == 1, "no retry inside the backoff window"
    clock.now += 5
    report.report()
    assert len(sent(cloud)) == 2
    clock.now += 9
    report.report()
    assert len(sent(cloud)) == 2, "the window doubles after a second failure"
    clock.now += 1
    report.report()
    assert len(sent(cloud)) == 3


def test_an_expired_token_is_refreshed_and_the_call_repeated(tmp_path):
    expired = CloudError("invalid_token", "expired", 401)
    cloud = FakeCloud(report_instance_projects=[expired, None],
                      refresh=[{"access_token": "at-2", "refresh_token": "rt-2",
                                "expires_in": 900}])
    report, _, _ = make(tmp_path, cloud, {})
    report.report()
    assert cloud.names() == ["report_instance_projects", "refresh",
                             "report_instance_projects"]
    assert cloud.calls[-1][1] == "at-2"


def test_nothing_is_sent_while_signed_out(tmp_path):
    cloud = FakeCloud()
    report, _, _ = make(tmp_path, cloud, {"shop": (STARTED_OK, WEB)}, signed_in=False)
    report.report()
    assert cloud.calls == []


def _pass_reports(tmp_path, **site):
    from tests.runtime.api.conftest import _make_env
    # Every other pass's call fails on purpose; each pass fails on its own.
    cloud = FakeCloud(report_instance_projects=[None])
    for env in _make_env(tmp_path, cloud=cloud, **site):
        env.repos.account.update(access_token="at", refresh_token="rt",
                                 access_expires_at=10**12, org_id="org-1")
        env.app.state.services.sync.run_once()
    return "report_instance_projects" in cloud.names()


def test_a_kitchen_reports_its_projects_from_the_sync_loop(tmp_path):
    assert _pass_reports(tmp_path, site_host="foo.eggie.io", domain="foo.eggie.space")


def test_a_local_vm_never_reports_projects(tmp_path):
    assert not _pass_reports(tmp_path)
