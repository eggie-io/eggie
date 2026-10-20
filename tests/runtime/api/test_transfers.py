"""The kitchen's transfer entry point: who may open a push, where it lands,
and what an import replaces and keeps."""
import logging
import os
import shutil
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from eggie_api.domain.push import build_manifest, data_plan
from eggie_api.infra import bundle as bundle_io
from eggie_api.infra.cloud import CloudError, CloudUnavailable
from eggie_api.infra.runner import Completed
from tests.runtime.api.conftest import BROWSER, _make_env
from tests.runtime.api.fake_cloud import FakeCloud
from tests.runtime.api.route_sweep import every_route

GIB = 1024 ** 3
H = {"Host": "foo.eggie.io"}

COMPOSE_SHOP = """
services:
  web:
    image: nginx
    ports: ["8080:80"]
  db:
    image: postgres:16
    volumes: ["./pgdata:/var/lib/postgresql/data", "db-data:/backups"]
volumes:
  db-data: {}
"""


def grant(project_id="c-1", name="My Shop", ticket_id="t-1"):
    return {"ticket_id": ticket_id, "org_id": "org-1", "user_id": "u-1",
            "source_device_id": "dev-1",
            "project": {"id": project_id, "name": name, "slug": "my-shop",
                        "client_ref": "dev-1/shop"}}


def _mounts(argv):
    """`-v host:guest[:ro]` pairs of a docker run argv, guest -> host."""
    out = {}
    for i, word in enumerate(argv):
        if word == "-v":
            host, guest = argv[i + 1].split(":")[:2]
            out[guest] = host
    return out


class Helper:
    """Does what the root helper container would, on real temp dirs."""

    def __init__(self, tmp_path):
        self.volumes = tmp_path / "docker-volumes"
        self.gate = None
        self.moves = []

    def __call__(self, argv):
        mounts = _mounts(argv)
        if "unpack" in argv:
            if self.gate is not None:
                assert self.gate.wait(5)
            name = argv[argv.index("--bundle") + 1].removeprefix("/in/")
            bundle_io.unpack(Path(mounts["/in"]) / name, Path(mounts["/dest"]))
        elif "restore" in argv:
            target = self.volumes / mounts["/vol"]
            target.mkdir(parents=True, exist_ok=True)
            bundle_io.restore_volume(Path(mounts["/src"]), target)
        elif "mv" in argv:
            src, dst = argv[-2], argv[-1]
            self.moves.append((src, dst))
            mode = os.stat(src).st_mode
            os.chmod(src, 0o755)
            os.rename(src, dst)
            os.chmod(dst, mode)
        elif "rm" in argv:
            shutil.rmtree(argv[-1], ignore_errors=True)
        return Completed(0, "", "")


class FakeTimers:
    """Stands in for threading.Timer; `fire()` runs every live one."""

    def __init__(self):
        self.made = []

    def __call__(self, seconds, fn):
        timer = type("T", (), {})()
        timer.seconds, timer.fn, timer.live = seconds, fn, False
        timer.start = lambda: setattr(timer, "live", True)
        timer.cancel = lambda: setattr(timer, "live", False)
        self.made.append(timer)
        return timer

    def fire(self):
        for timer in self.made:
            if timer.live:
                timer.live = False
                timer.fn()


@pytest.fixture
def kitchen(tmp_path):
    cloud, sleeps, free, timers = FakeCloud(), [], {"bytes": 100 * GIB}, FakeTimers()
    for ns in _make_env(tmp_path, cloud=cloud, sleep=sleeps.append,
                        free_bytes=lambda: free["bytes"], timer=timers,
                        site_host="foo.eggie.io", domain="foo.eggie.space"):
        ns.cloud, ns.sleeps, ns.free, ns.timers = cloud, sleeps, free, timers
        ns.repos.account.update(access_token="at", refresh_token="rt",
                                access_expires_at=10**12, org_id="org-1")
        ns.helper = Helper(tmp_path)
        ns.runner.on_docker_run = ns.helper
        ns.http = TestClient(ns.app, raise_server_exceptions=False, headers=H)
        ns.local = tmp_path / "local" / "shop"
        ns.services = ns.app.state.services
        yield ns


def local_project(k, compose=COMPOSE_SHOP, files=None):
    shutil.rmtree(k.local, ignore_errors=True)
    k.local.mkdir(parents=True)
    (k.local / "docker-compose.yml").write_text(compose)
    for rel, text in (files or {"src/app.py": "print('new')\n",
                                "pgdata/PG_VERSION": "16 on my laptop\n"}).items():
        (k.local / rel).parent.mkdir(parents=True, exist_ok=True)
        (k.local / rel).write_text(text)


def make_bundle(k, *, data=False, secrets=None, volumes=None, name="b.tar"):
    plan = data_plan(yaml.safe_load((k.local / "docker-compose.yml").read_text()))
    manifest = build_manifest(data=data, plan=plan, secrets=secrets or {})
    out = k.local.parent / name
    bundle_io.write_bundle(out, k.local, manifest, volumes=volumes or {},
                           data_paths=plan.data_paths, include_data=data)
    return out


def kitchen_project(k, pid="my-shop", cloud_id="c-1", files=None, status="stopped"):
    folder = k.config.projects_root / pid
    folder.mkdir(parents=True)
    (folder / "docker-compose.yml").write_text(COMPOSE_SHOP)
    for rel, text in (files or {"src/old.py": "old\n",
                                "pgdata/PG_VERSION": "16 on the kitchen\n"}).items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text)
    k.repos.projects.add(pid, str(folder), "foo.eggie.space", status=status)
    k.repos.pushed_projects.claim(pid, cloud_id, "dev-1", 1.0)
    return folder


def open_push(k, *, ticket="tk-1", size=1000, data="no", replace="none", **extra):
    headers = {"Eggie-Transfer-Ticket": ticket,
               "Eggie-Transfer-Size": None if size is None else str(size),
               "Eggie-Transfer-Data": data, "Eggie-Transfer-Replace": replace}
    headers.update(extra)
    return k.http.post("/api/transfers", headers={k: v for k, v in headers.items()
                                                  if v is not None})


def upload(k, opened, path, **headers):
    return k.http.put(f"/api/transfers/{opened['push_id']}/bundle",
                      content=path.read_bytes(),
                      headers={"Eggie-Transfer-Token": opened["token"], **headers})


def finished(k, opened) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = k.http.get(f"/api/transfers/{opened['push_id']}",
                            headers={"Eggie-Transfer-Token": opened["token"]}).json()
        if status["state"] != "running":
            return status
        time.sleep(0.01)
    raise AssertionError("the import never finished")


def push(k, path, *, data="no", replace="none", ticket_id="t-1", complete=(None,)):
    k.cloud.replies.update(redeem_transfer_ticket=[grant(ticket_id=ticket_id)],
                           complete_transfer=list(complete))
    opened = open_push(k, size=path.stat().st_size, data=data, replace=replace)
    assert opened.status_code == 201, opened.text
    assert upload(k, opened.json(), path).status_code == 202
    return finished(k, opened.json())


def settle(k, pid):
    job = k.jobs.active_for(pid)
    if job is not None:
        k.jobs.wait(job.id, timeout=5)


def _paths(app):
    return {getattr(r, "path", "") for r in every_route(app)}


def test_transfer_routes_exist_only_on_a_site(env):
    assert not {"/api/transfers", "/api/ready"} & _paths(env.app)
    resp = TestClient(env.app, headers={**BROWSER, "Origin": "http://localhost:41080"}).post(
        "/api/transfers", headers={"Eggie-Transfer-Ticket": "tk-1"})
    assert resp.json()["error"]["code"] == "not_signed_in"


def test_a_site_serves_the_transfer_routes(kitchen):
    assert {"/api/transfers", "/api/transfers/{push_id}/bundle",
            "/api/transfers/{push_id}", "/api/ready"} <= _paths(kitchen.app)


@pytest.mark.parametrize("host", ["localhost:41080", "my-shop.foo.eggie.space", "evil.example"])
def test_wrong_host_is_refused_before_anything(kitchen, host):
    resp = open_push(kitchen, Host=host)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_host"
    assert kitchen.cloud.calls == []


def test_api_token_and_session_are_not_credentials(kitchen):
    code = kitchen.client.post("/sessions/handoff").json()["code"]
    browser = TestClient(kitchen.app, headers={**H, "Origin": "https://foo.eggie.io"})
    signed_in = browser.post("/api/session", json={"code": code})
    # The cookie is Secure and the test client speaks http: send it by hand.
    session = {"Cookie": signed_in.headers["set-cookie"].split(";", 1)[0]}

    for creds in ({"Authorization": "Bearer test-token"}, session):
        resp = kitchen.http.post("/api/transfers", headers={
            **creds, "Eggie-Transfer-Size": "10", "Eggie-Transfer-Data": "no",
            "Eggie-Transfer-Replace": "none"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "bad_transfer"
    assert kitchen.cloud.calls == []


def test_missing_size_is_length_required_and_bad_values_are_bad_transfer(kitchen):
    assert open_push(kitchen, size=None).status_code == 411
    for bad in ({"size": "12abc"}, {"data": "maybe"}, {"replace": "everything"},
                {"ticket": ""}):
        resp = open_push(kitchen, **bad)
        assert resp.json()["error"]["code"] == "bad_transfer", bad
    assert kitchen.cloud.calls == []


def test_refused_ticket_relays_the_service_envelope(kitchen):
    kitchen.cloud.replies["redeem_transfer_ticket"] = [
        CloudError("transfer_ticket_invalid", "m", 403)]
    resp = open_push(kitchen)
    assert resp.status_code == 403
    assert resp.json() == {"error": {"code": "transfer_ticket_invalid", "message": "m"}}
    assert kitchen.repos.projects.list() == []


def test_an_unreachable_service_is_cloud_unavailable(kitchen):
    kitchen.cloud.replies["redeem_transfer_ticket"] = [CloudUnavailable("down")]
    resp = open_push(kitchen)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "cloud_unavailable"


def test_existing_project_needs_replace(kitchen):
    kitchen_project(kitchen)
    kitchen.cloud.replies["redeem_transfer_ticket"] = [grant()]
    resp = open_push(kitchen, replace="none")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "already_on_kitchen"
    assert kitchen.services.locks.acquire("my-shop")


def test_data_into_existing_needs_files_and_data(kitchen):
    kitchen_project(kitchen)
    kitchen.cloud.replies["redeem_transfer_ticket"] = [grant(), grant()]
    resp = open_push(kitchen, data="yes", replace="files")
    assert resp.json()["error"]["code"] == "confirm_replace_data"
    assert open_push(kitchen, data="yes", replace="files-and-data").status_code == 201


def test_a_held_project_lock_is_kitchen_busy(kitchen):
    kitchen_project(kitchen)
    kitchen.services.locks.acquire("my-shop")
    kitchen.cloud.replies["redeem_transfer_ticket"] = [grant()]
    resp = open_push(kitchen, replace="files")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "kitchen_busy"


def test_disk_check_uses_twice_the_size_plus_a_gib(kitchen):
    kitchen.cloud.replies["redeem_transfer_ticket"] = [grant(), grant()]
    kitchen.free["bytes"] = 2 * 1000 + GIB - 1
    resp = open_push(kitchen, size=1000)
    assert resp.status_code == 507
    assert resp.json()["error"]["code"] == "kitchen_disk_full"
    # A refused open leaves no project behind.
    assert kitchen.repos.projects.list() == []
    assert kitchen.repos.pushed_projects.by_cloud_id("c-1") is None

    kitchen.free["bytes"] = 2 * 1000 + GIB
    assert open_push(kitchen, size=1000).status_code == 201


def test_name_taken_by_a_kitchen_project_gets_a_suffix(kitchen):
    kitchen.repos.projects.add("my-shop", "/x", "foo.eggie.space")
    kitchen.cloud.replies["redeem_transfer_ticket"] = [grant()]
    assert open_push(kitchen).status_code == 201
    assert kitchen.repos.pushed_projects.by_cloud_id("c-1")["local_id"] == "my-shop-2"
    assert kitchen.repos.projects.get("my-shop-2") is not None


def test_a_push_into_a_taken_name_is_named_by_the_kitchen_not_its_project_yml(kitchen):
    kitchen_project(kitchen, pid="my-shop", cloud_id="c-other")
    local_project(kitchen, files={
        "src/app.py": "new\n",
        ".eggie/project.yml": "id: my-shop\nweb:\n  - service: web\n    port: 80\n"})

    status = push(kitchen, make_bundle(kitchen))

    assert status["state"] == "done", status
    assert status["result"]["kitchen_project"] == "my-shop-2"
    assert status["result"]["urls"] == ["https://my-shop-2.foo.eggie.space"]
    pushed = yaml.safe_load((kitchen.config.projects_root / "my-shop-2" / ".eggie"
                             / "project.yml").read_text())
    assert pushed == {"id": "my-shop-2", "web": [{"service": "web", "port": 80}]}


def test_full_import_replaces_files_keeps_data_paths_and_completes(kitchen):
    folder = kitchen_project(kitchen, status="started_ok")
    local_project(kitchen)
    old_file_at_down = []
    exec_ = kitchen.runner.exec

    def watch(argv, **kwargs):
        if "down" in argv:
            old_file_at_down.append((folder / "src" / "old.py").exists())
        return exec_(argv, **kwargs)

    kitchen.runner.exec = watch

    status = push(kitchen, make_bundle(kitchen), replace="files")

    assert status["state"] == "done", status
    assert status["result"] == {"kitchen_project": "my-shop",
                                "urls": ["https://my-shop.foo.eggie.space"],
                                "secrets_missing": 0}
    assert not (folder / "src" / "old.py").exists()
    assert (folder / "src" / "app.py").read_text() == "print('new')\n"
    assert (folder / "pgdata" / "PG_VERSION").read_text() == "16 on the kitchen\n"
    # The old stack was stopped before its files went away.
    assert old_file_at_down == [True]
    assert kitchen.cloud.calls[-1] == ("complete_transfer", "at", "t-1")
    assert kitchen.repos.pushed_projects.by_cloud_id("c-1")["last_pushed_at"] is not None
    assert list((kitchen.config.transfers_root / "in").iterdir()) == []
    settle(kitchen, "my-shop")
    assert kitchen.runner.argv_containing("up")


def test_a_data_folder_the_api_cannot_move_goes_through_the_root_helper(kitchen):
    folder = kitchen_project(kitchen)
    # A database's data folder is 0700 and owned by the database's user.
    (folder / "pgdata").chmod(0o500)
    local_project(kitchen)

    status = push(kitchen, make_bundle(kitchen), replace="files")

    assert status["state"] == "done", status
    assert (folder / "pgdata" / "PG_VERSION").read_text() == "16 on the kitchen\n"
    assert any(dst == str(folder / "pgdata") for _, dst in kitchen.helper.moves)


def test_a_data_push_replaces_files_and_restores_each_volume(kitchen, tmp_path):
    folder = kitchen_project(kitchen)
    local_project(kitchen)
    source = tmp_path / "vol-db-data"
    source.mkdir()
    (source / "dump.sql").write_text("select 1;\n")

    status = push(kitchen, make_bundle(kitchen, data=True, volumes={"db-data": source}),
                  data="yes", replace="files-and-data")

    assert status["state"] == "done", status
    assert (folder / "pgdata" / "PG_VERSION").read_text() == "16 on my laptop\n"
    assert (kitchen.helper.volumes / "my-shop_db-data" / "dump.sql").read_text() == "select 1;\n"
    removed = kitchen.runner.argv_containing("rm")
    assert any(a[1:3] == ["volume", "rm"] and a[-1] == "my-shop_db-data" for a in removed)
    created = [a for a in kitchen.runner.argv_containing("create") if a[1] == "volume"]
    assert created and "com.docker.compose.project=my-shop" in created[0]


def test_import_with_missing_secrets_waits_instead_of_starting(kitchen):
    local_project(kitchen)

    status = push(kitchen, make_bundle(kitchen, secrets={"STRIPE_KEY": "from the dashboard"}))

    assert status["result"]["secrets_missing"] == 1
    row = kitchen.repos.projects.get("my-shop")
    assert row["start_when_ready"] == 1
    assert kitchen.runner.argv_containing("up") == []
    assert kitchen.services.secrets.list("my-shop")["requested"] == [
        {"name": "STRIPE_KEY", "hint": "from the dashboard"}]


def test_complete_project_not_found_fails_and_keeps_files(kitchen):
    local_project(kitchen)

    status = push(kitchen, make_bundle(kitchen),
                  complete=[CloudError("project_not_found", "gone", 404)])

    assert status["state"] == "failed"
    assert status["result"] == {"code": "project_not_found"}
    assert "deleted from your account" in status["detail"]
    assert (kitchen.config.projects_root / "my-shop" / "src" / "app.py").exists()
    assert kitchen.repos.pushed_projects.by_cloud_id("c-1")["last_pushed_at"] is None
    assert kitchen.services.locks.acquire("my-shop")


@pytest.mark.parametrize("status", ["started_ok", "crash_looping"])
def test_a_failed_import_stops_then_starts_again_a_project_that_was_up(kitchen, status):
    folder = kitchen_project(kitchen, status=status)
    local_project(kitchen)

    result = push(kitchen, make_bundle(kitchen), replace="files",
                  complete=[CloudError("project_not_found", "gone", 404)])
    settle(kitchen, "my-shop")

    assert result["state"] == "failed"
    assert kitchen.runner.argv_containing("down")
    assert kitchen.runner.argv_containing("up")
    assert (folder / "src" / "app.py").exists()


def test_a_failed_import_with_missing_secrets_waits_for_them(kitchen):
    kitchen_project(kitchen, status="started_ok")
    local_project(kitchen)

    result = push(kitchen, make_bundle(kitchen, secrets={"STRIPE_KEY": ""}), replace="files",
                  complete=[CloudError("project_not_found", "gone", 404)])
    settle(kitchen, "my-shop")

    assert result["state"] == "failed"
    assert kitchen.repos.projects.get("my-shop")["start_when_ready"] == 1
    assert kitchen.runner.argv_containing("up") == []


def test_complete_is_retried_three_times_on_an_unreachable_service(kitchen):
    local_project(kitchen)
    path = make_bundle(kitchen)

    ok = push(kitchen, path, complete=[CloudUnavailable("x"), CloudUnavailable("x"), None])
    assert ok["state"] == "done"
    assert kitchen.sleeps == [1, 3]

    settle(kitchen, "my-shop")
    kitchen.sleeps.clear()
    gone = push(kitchen, path, replace="files", complete=[CloudUnavailable("x")] * 4)
    assert gone["result"] == {"code": "cloud_unavailable"}
    assert kitchen.sleeps == [1, 3, 9]


def test_a_bad_manifest_fails_before_touching_files_or_the_stack(kitchen):
    folder = kitchen_project(kitchen, status="started_ok")
    # The manifest names a volume the pushed compose file does not have.
    local_project(kitchen, compose="services:\n  web:\n    image: nginx\n")
    path = kitchen.local.parent / "bad.tar"
    bundle_io.write_bundle(path, kitchen.local, {"format": 1, "data": False,
                                                 "volumes": ["db-data"], "data_paths": [],
                                                 "secrets": []},
                           volumes={}, data_paths=[], include_data=False)

    status = push(kitchen, path, replace="files")

    assert status["result"] == {"code": "bad_manifest"}
    assert (folder / "src" / "old.py").exists()
    assert list((kitchen.config.transfers_root / "in").iterdir()) == []
    settle(kitchen, "my-shop")
    assert kitchen.runner.argv_containing("down") == []
    assert kitchen.runner.argv_containing("up") == []


@pytest.mark.parametrize("status, starts", [("stopped", False), ("started_ok", True)])
def test_a_failed_replace_puts_the_old_folder_back(kitchen, status, starts):
    compose = COMPOSE_SHOP.replace("./pgdata", "./data/pg")
    folder = kitchen_project(kitchen, files={"src/old.py": "old\n", "data/pg/PG_VERSION": "16\n"},
                             status=status)
    (folder / "docker-compose.yml").write_text(compose)
    # The pushed tree's `data` is a link out of the project; moving the
    # kitchen's data/pg through it would write outside the tree.
    local_project(kitchen, compose=compose, files={"src/app.py": "new\n"})
    (kitchen.local / "data").symlink_to("/tmp")

    status = push(kitchen, make_bundle(kitchen), replace="files")

    assert status["state"] == "failed"
    assert (folder / "src" / "old.py").exists()
    assert not (folder / "src" / "app.py").exists()
    assert (folder / "data" / "pg" / "PG_VERSION").read_text() == "16\n"
    settle(kitchen, "my-shop")
    assert bool(kitchen.runner.argv_containing("up")) is starts
    assert kitchen.services.locks.acquire("my-shop")


def test_a_failed_put_back_keeps_the_old_folder_outside_the_inbox(kitchen, monkeypatch):
    compose = COMPOSE_SHOP.replace("./pgdata", "./data/pg")
    folder = kitchen_project(kitchen, files={"src/old.py": "old\n", "data/pg/PG_VERSION": "16\n"})
    (folder / "docker-compose.yml").write_text(compose)
    local_project(kitchen, compose=compose, files={"src/app.py": "new\n"})
    (kitchen.local / "data").symlink_to("/tmp")
    rename = os.rename

    def stuck(src, dst):
        if str(dst) == str(folder) and str(src).endswith("/old"):
            raise OSError(5, "Input/output error")
        rename(src, dst)

    monkeypatch.setattr(os, "rename", stuck)

    status = push(kitchen, make_bundle(kitchen), replace="files")

    assert status["state"] == "failed"
    assert status["result"] == {"code": "bad_bundle"}
    [kept] = (kitchen.config.transfers_root / "kept").glob("my-shop-*")
    assert (kept / "src" / "old.py").read_text() == "old\n"
    assert (kept / "data" / "pg" / "PG_VERSION").read_text() == "16\n"
    assert str(kept) in status["detail"]
    assert list((kitchen.config.transfers_root / "in").iterdir()) == []


def test_the_bundle_is_gone_once_unpacked_while_the_import_goes_on(kitchen):
    kitchen_project(kitchen, status="started_ok")
    local_project(kitchen)
    inbox = kitchen.config.transfers_root / "in"
    bundles_at_down = []
    exec_ = kitchen.runner.exec

    def watch(argv, **kwargs):
        if "down" in argv:
            bundles_at_down.append(list(inbox.glob("*.tar")))
        return exec_(argv, **kwargs)

    kitchen.runner.exec = watch

    status = push(kitchen, make_bundle(kitchen), replace="files")

    assert status["state"] == "done", status
    assert bundles_at_down == [[]]


def test_a_files_push_named_after_another_kitchen_project_is_refused(kitchen):
    other = kitchen_project(kitchen, pid="my-shop", cloud_id="c-other", status="started_ok")
    local_project(kitchen, compose="name: my-shop\n" + COMPOSE_SHOP)

    status = push(kitchen, make_bundle(kitchen))

    assert status["result"] == {"code": "bad_bundle"}
    assert "belongs to another project" in status["detail"]
    assert (other / "src" / "old.py").exists()
    assert kitchen.runner.argv_containing("down") == []


def test_failed_upload_releases_the_lock(kitchen):
    local_project(kitchen)
    path = make_bundle(kitchen)
    kitchen.cloud.replies["redeem_transfer_ticket"] = [grant(), grant()]
    opened = open_push(kitchen, size=path.stat().st_size + 1).json()

    resp = upload(kitchen, opened, path)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "bad_transfer"
    assert list((kitchen.config.transfers_root / "in").glob("*.tar")) == []
    # The project made for the failed push is gone too, so a retry is a first push again.
    assert open_push(kitchen, size=path.stat().st_size).status_code == 201


def test_a_new_open_replaces_one_that_never_uploaded(kitchen):
    kitchen.cloud.replies["redeem_transfer_ticket"] = [grant(), grant()]
    first = open_push(kitchen).json()
    second = open_push(kitchen)
    assert second.status_code == 201
    stale = kitchen.http.get(f"/api/transfers/{first['push_id']}",
                             headers={"Eggie-Transfer-Token": first["token"]})
    assert stale.status_code == 404


def test_an_open_push_expires_after_ten_minutes(kitchen):
    kitchen.cloud.replies["redeem_transfer_ticket"] = [grant()]
    opened = open_push(kitchen).json()
    assert [t.seconds for t in kitchen.timers.made] == [600]

    kitchen.timers.fire()

    assert kitchen.services.locks.acquire("my-shop")
    assert kitchen.repos.projects.get("my-shop") is None
    assert kitchen.http.get(f"/api/transfers/{opened['push_id']}", headers={
        "Eggie-Transfer-Token": opened["token"]}).status_code == 404


def test_an_upload_stops_the_expiry(kitchen):
    local_project(kitchen)
    status = push(kitchen, make_bundle(kitchen))
    kitchen.timers.fire()
    assert status["state"] == "done"
    assert kitchen.repos.projects.get("my-shop") is not None


def test_a_wrong_token_upload_writes_nothing_and_keeps_the_push(kitchen):
    local_project(kitchen)
    path = make_bundle(kitchen)
    kitchen.cloud.replies.update(redeem_transfer_ticket=[grant()], complete_transfer=[None])
    opened = open_push(kitchen, size=path.stat().st_size).json()

    resp = upload(kitchen, {**opened, "token": "wrong"}, path)

    assert resp.status_code == 404
    inbox = kitchen.config.transfers_root / "in"
    assert not inbox.exists() or list(inbox.iterdir()) == []
    assert upload(kitchen, opened, path).status_code == 202
    assert finished(kitchen, opened)["state"] == "done"


def test_a_bundle_with_data_the_open_did_not_declare_is_refused(kitchen, tmp_path):
    folder = kitchen_project(kitchen)
    local_project(kitchen)
    source = tmp_path / "vol"
    source.mkdir()

    status = push(kitchen, make_bundle(kitchen, data=True, volumes={"db-data": source}),
                  data="no", replace="files")

    assert status["result"] == {"code": "bad_bundle"}
    assert (folder / "src" / "old.py").exists()
    assert (folder / "pgdata" / "PG_VERSION").read_text() == "16 on the kitchen\n"
    assert not [a for a in kitchen.runner.calls if a[1:3] == ["volume", "rm"]]


def test_a_volume_named_outside_the_project_is_refused(kitchen, tmp_path):
    compose = COMPOSE_SHOP.replace("db-data: {}", "db-data: {name: blog_db-data}")
    kitchen_project(kitchen)
    local_project(kitchen, compose=compose)
    source = tmp_path / "vol"
    source.mkdir()

    status = push(kitchen, make_bundle(kitchen, data=True, volumes={"db-data": source}),
                  data="yes", replace="files-and-data")

    assert status["result"] == {"code": "bad_bundle"}
    assert not [a for a in kitchen.runner.calls if a[1:3] == ["volume", "rm"]]


def test_status_needs_the_token_and_hides_unknown_ids(kitchen):
    kitchen.cloud.replies["redeem_transfer_ticket"] = [grant()]
    opened = open_push(kitchen).json()
    url = f"/api/transfers/{opened['push_id']}"

    wrong = kitchen.http.get(url, headers={"Eggie-Transfer-Token": "nope"})
    unknown = kitchen.http.get("/api/transfers/abc", headers={"Eggie-Transfer-Token": "nope"})
    assert wrong.status_code == unknown.status_code == 404
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "transfer_not_found"
    assert kitchen.http.get(url, headers={"Eggie-Transfer-Token": opened["token"]}
                            ).status_code == 200


def test_rate_limit_answers_429(kitchen):
    kitchen.cloud.replies["redeem_transfer_ticket"] = [
        CloudError("transfer_ticket_invalid", "m", 403)] * 10
    for _ in range(10):
        assert open_push(kitchen).status_code == 403
    resp = open_push(kitchen)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "kitchen_busy"
    assert len(kitchen.cloud.calls) == 10


def _refused_opens(k, n):
    k.cloud.replies["redeem_transfer_ticket"] = [
        CloudError("transfer_ticket_invalid", "m", 403)] * n


def test_one_sender_at_its_limit_does_not_hold_back_another(kitchen):
    _refused_opens(kitchen, 11)
    first = {"X-Forwarded-For": "203.0.113.1, 10.0.0.1"}
    for _ in range(10):
        assert open_push(kitchen, **first).status_code == 403
    assert open_push(kitchen, **first).json()["error"]["code"] == "kitchen_busy"

    assert open_push(kitchen, **{"X-Forwarded-For": "203.0.113.2, 10.0.0.1"}).status_code == 403
    assert len(kitchen.cloud.calls) == 11


def test_all_senders_together_are_held_to_sixty_a_minute(kitchen):
    _refused_opens(kitchen, 60)
    for n in range(60):
        resp = open_push(kitchen, **{"X-Forwarded-For": f"203.0.113.{n // 10}"})
        assert resp.status_code == 403
    resp = open_push(kitchen, **{"X-Forwarded-For": "198.51.100.7"})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "kitchen_busy"
    assert len(kitchen.cloud.calls) == 60


def test_ready_reflects_sign_in_and_docker(kitchen):
    assert kitchen.http.get("/api/ready").json() == {"ready": True}
    kitchen.runner.docker_version = Completed(1, "", "Cannot connect to the Docker daemon")
    resp = kitchen.http.get("/api/ready")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "not_ready"
    kitchen.runner.docker_version = Completed(0, "27.1.1", "")
    kitchen.repos.account.update(access_token=None)
    assert kitchen.http.get("/api/ready").status_code == 503


def test_ticket_never_reaches_a_log_line(kitchen, caplog):
    caplog.set_level(logging.DEBUG)
    local_project(kitchen)
    path = make_bundle(kitchen)
    kitchen.cloud.replies.update(
        redeem_transfer_ticket=[CloudError("transfer_ticket_invalid", "m", 403),
                                grant()],
        complete_transfer=[CloudError("transfer_not_found", "x", 404)])
    open_push(kitchen, ticket="tk-secret-one")
    opened = open_push(kitchen, ticket="tk-secret-two", size=path.stat().st_size).json()
    upload(kitchen, opened, path)
    finished(kitchen, opened)

    assert "tk-secret" not in caplog.text
    assert opened["token"] not in caplog.text


def test_deleting_a_kitchen_project_forgets_that_it_was_pushed(kitchen):
    kitchen_project(kitchen)
    assert kitchen.client.delete("/projects/my-shop").status_code == 200
    assert kitchen.repos.pushed_projects.by_cloud_id("c-1") is None
