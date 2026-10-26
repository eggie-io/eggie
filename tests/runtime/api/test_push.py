import json
import tarfile
import threading

import pytest
from fastapi.testclient import TestClient

from eggie_api.infra.bundle import write_bundle
from eggie_api.infra import docker
from eggie_api.infra.cloud import CloudError
from eggie_api.infra.instance import InstanceError, InstanceUnreachable
from eggie_api.infra.runner import Completed
from tests.runtime.api.conftest import BROWSER, _make_env
from tests.runtime.api.fake_cloud import FakeCloud
from tests.runtime.api.fake_instance import FakeInstance

ORIGIN = {"Origin": "http://localhost:41080"}

COMPOSE_SHOP = """
services:
  web:
    image: nginx
    ports: ["8080:80"]
  db:
    image: postgres:latest
    volumes: ["db-data:/var/lib/postgresql/data"]
volumes:
  db-data: {}
"""

INSTANCE = {"id": "k1", "display_name": "My kitchen", "console_hostname": "k1.eggie.io",
           "site_hostname": "k1.eggie.space", "readiness": "ready", "is_owner": True,
           "open_console_url": "https://app.eggie.io/kitchens/open?host=k1.eggie.io",
           "manage_url": "https://app.eggie.io/kitchens/k1"}
TARGETS = {"instances": [INSTANCE], "create_instance_url": "https://app.eggie.io/kitchens/new"}
OPENED = {"push_id": "p1", "token": "push-token"}
WORKING = {"state": "running", "phase": "unpacking", "detail": "", "result": None}
DONE = {"state": "done", "phase": "done", "detail": "",
        "result": {"instance_project": "shop", "urls": ["https://shop.k1.eggie.space"],
                   "secrets_missing": 2}}


class FakeTime:
    """A clock that only moves when the push sleeps."""

    def __init__(self):
        self.now = 1_792_500_000.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


def ticket(n=1):
    return {"ticket": f"tk-{n}", "expires_at": "2026-10-20T12:00:00Z",
            "instance_id": "k1", "console_hostname": "k1.eggie.io"}


@pytest.fixture
def env(tmp_path):
    cloud, instance, fake_time = FakeCloud(), FakeInstance(), FakeTime()
    for ns in _make_env(tmp_path, cloud=cloud, instance=instance, sleep=fake_time.sleep,
                        clock=fake_time.clock):
        ns.cloud, ns.instance, ns.time, ns.sleeps = cloud, instance, fake_time, fake_time.sleeps
        ns.client.post("/projects", json={"id": "shop"})
        (ns.config.projects_root / "shop" / "docker-compose.yml").write_text(COMPOSE_SHOP)
        ns.repos.cloud_projects.map("shop", "c-shop", "org-1")
        ns.repos.account.update(access_token="at", refresh_token="rt",
                                access_expires_at=10**12, org_id="org-1")
        ns.runner.on_docker_run = _pack_hook(ns, tmp_path)
        code = ns.client.post("/sessions/handoff").json()["code"]
        ns.browser = TestClient(ns.app, headers={**BROWSER, **ORIGIN})
        assert ns.browser.post("/api/session", json={"code": code}).status_code == 200
        yield ns


def _pack_hook(env, tmp_path):
    """Does what the root helper would: writes the bundle the argv names."""
    def hook(argv):
        if "pack" not in argv:
            return Completed(0, "", "")
        out = env.config.transfers_root / "out"

        def arg(flag):
            return argv[argv.index(flag) + 1].removeprefix("/out/")

        manifest = json.loads((out / arg("--manifest")).read_text())
        volumes = {}
        for i, word in enumerate(argv):
            if word == "--volume":
                key = argv[i + 1].split("=", 1)[0]
                volumes[key] = tmp_path / "vol" / key
                volumes[key].mkdir(parents=True, exist_ok=True)
        write_bundle(out / arg("--out"), env.config.projects_root / "shop", manifest,
                     volumes=volumes, data_paths=manifest["data_paths"],
                     include_data="--data" in argv)
        return Completed(0, "", "")
    return hook


def script(env, *, targets=(TARGETS,), mints=(), opens=(), uploads=(), statuses=()):
    env.cloud.replies.update(transfer_targets=list(targets),
                             mint_transfer_ticket=list(mints))
    env.instance.replies.update(open=list(opens), upload=list(uploads),
                               status=list(statuses))


def push(env, *, data=True, replace_data=False) -> dict:
    resp = env.browser.post("/api/projects/shop/push",
                            json={"instance_id": "k1", "data": data,
                                  "replace_data": replace_data})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    env.jobs.wait(job_id, timeout=5)
    return env.browser.get(f"/api/jobs/{job_id}").json()


def record(env):
    env.repos.instance_pushes.record("shop", "k1", console_hostname="k1.eggie.io",
                                    instance_project="shop", urls=["https://shop.k1.eggie.space"],
                                    secrets_missing=0, at=1792510000)


def test_targets_merge_service_instances_with_local_records(env):
    script(env)
    record(env)

    body = env.browser.get("/api/projects/shop/push").json()

    assert body["instances"] == [INSTANCE]
    assert body["create_instance_url"] == TARGETS["create_instance_url"]
    assert body["pushed"]["k1"]["instance_project"] == "shop"
    assert body["databases"] == [{"service": "db", "image": "postgres:latest",
                                  "floating": True}]
    assert body["running"] is False


def test_push_happy_path_mints_opens_uploads_and_records(env):
    script(env, mints=[ticket()], opens=[OPENED], uploads=[None],
           statuses=[WORKING, DONE])

    job = push(env)

    assert job["state"] == "done", job
    assert env.cloud.calls == [("transfer_targets", "at"),
                               ("mint_transfer_ticket", "at", "k1", "c-shop")]
    assert env.instance.names() == ["open", "upload", "status", "status"]
    _, host, sent_ticket, size, data, replace = env.instance.calls[0]
    assert (host, sent_ticket, data, replace) == ("k1.eggie.io", "tk-1", True, "none")
    assert job["progress"] == {"done": size, "total": size}
    assert env.repos.instance_pushes.for_project("shop")["k1"]["instance_project"] == "shop"
    assert job["result"]["instance_name"] == "My kitchen"
    assert job["result"]["secrets_missing"] == 2
    assert job["result"]["open_secrets_url"] == (
        INSTANCE["open_console_url"] + "&return_path=%2Fp%2Fshop%2Fsecrets")


def test_bundle_carries_secret_names_never_values(env):
    env.client.put("/projects/shop/secrets/STRIPE_KEY", json={"value": "sk-very-secret"})
    env.client.put("/projects/shop/secret-requests/MAIL_KEY", json={"hint": "from Mailgun"})
    seen = {}

    def upload(host, push_id, token, path):
        seen["bytes"] = path.read_bytes()
        with tarfile.open(path) as tar:
            seen["manifest"] = json.load(tar.extractfile("manifest.json"))

    script(env, mints=[ticket()], opens=[OPENED], uploads=[upload], statuses=[DONE])

    assert push(env)["state"] == "done"
    assert seen["manifest"]["secrets"] == [{"name": "MAIL_KEY", "hint": "from Mailgun"},
                                           {"name": "STRIPE_KEY", "hint": ""}]
    assert b"sk-very-secret" not in seen["bytes"]


def test_a_one_file_db_bind_ships_as_a_file_not_as_data(env):
    folder = env.config.projects_root / "shop"
    (folder / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: postgres:16\n    volumes:\n"
        "      - ./pgdata:/var/lib/postgresql/data\n      - ./postgres.conf:/etc/postgres.conf\n"
        "      - ./fresh:/fresh\n")
    (folder / "pgdata").mkdir()
    (folder / "pgdata" / "PG_VERSION").write_text("16\n")
    (folder / "postgres.conf").write_text("max_connections = 20\n")
    seen = {}

    def upload(host, push_id, token, path):
        with tarfile.open(path) as tar:
            seen["manifest"] = json.load(tar.extractfile("manifest.json"))
            seen["names"] = set(tar.getnames())

    script(env, mints=[ticket()], opens=[OPENED], uploads=[upload], statuses=[DONE])

    assert push(env, data=False)["state"] == "done"
    # A source not there yet is still data: Docker creates it as a directory.
    assert seen["manifest"]["data_paths"] == ["fresh", "pgdata"]
    assert "files/postgres.conf" in seen["names"]
    assert "files/pgdata/PG_VERSION" not in seen["names"]


def test_record_is_written_when_the_instance_accepts_the_upload(env):
    script(env, mints=[ticket()], opens=[OPENED], uploads=[None],
           statuses=[InstanceUnreachable("gone")] * 31)

    job = push(env)

    assert job["state"] == "failed"
    assert job["result"]["code"] == "instance_unreachable"
    assert "k1" in env.repos.instance_pushes.for_project("shop")


def test_waiting_rides_out_a_minute_of_missed_polls(env):
    misses = [InstanceUnreachable("busy")] * 29 + [InstanceError("internal", "boom", 500)]
    script(env, mints=[ticket()], opens=[OPENED], uploads=[None],
           statuses=[WORKING, *misses, DONE])

    assert push(env)["state"] == "done"


def test_waiting_gives_up_after_a_minute_without_an_answer(env):
    script(env, mints=[ticket()], opens=[OPENED], uploads=[None],
           statuses=[WORKING] + [InstanceUnreachable("gone")] * 31 + [DONE])

    job = push(env)

    assert job["result"]["code"] == "instance_unreachable"
    assert env.instance.names().count("status") == 32


def test_an_import_still_running_after_two_hours_times_out(env):
    def two_hours_later(*_args):
        env.time.now += 2 * 3600
        return WORKING

    script(env, mints=[ticket()], opens=[OPENED], uploads=[None],
           statuses=[WORKING, two_hours_later, DONE])

    job = push(env)

    assert job["result"]["code"] == "instance_timeout"
    assert job["detail"] == "My kitchen is still importing. Open the kitchen to check on shop."


def test_repeat_push_with_data_needs_confirmation(env):
    record(env)

    resp = env.browser.post("/api/projects/shop/push",
                            json={"instance_id": "k1", "data": True})

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "confirm_replace_data"
    assert env.cloud.calls == []


def test_ticket_refused_is_retried_exactly_once(env):
    refused = InstanceError("transfer_ticket_invalid", "no", 403)
    script(env, mints=[ticket(1), ticket(2)], opens=[refused, refused])

    job = push(env)

    assert env.cloud.names().count("mint_transfer_ticket") == 2
    assert job["result"] == {"code": "transfer_ticket_invalid", "retry_after": None}
    assert job["detail"] == "The kitchen didn't accept the push. Try again."


def test_already_on_instance_is_recorded_and_not_retried(env):
    script(env, mints=[ticket()],
           opens=[InstanceError("already_on_instance", "exists", 409)])

    job = push(env)

    assert env.cloud.names().count("mint_transfer_ticket") == 1
    assert job["result"]["code"] == "already_on_instance"
    assert "k1" in env.repos.instance_pushes.for_project("shop")


def test_unreachable_instance_gets_three_attempts_with_fresh_tickets(env):
    script(env, mints=[ticket(1), ticket(2), ticket(3)],
           opens=[InstanceUnreachable("down")] * 3)

    job = push(env)

    assert [call[2] for call in env.instance.calls] == ["tk-1", "tk-2", "tk-3"]
    assert job["result"]["code"] == "instance_unreachable"


def test_rate_limit_reports_retry_after(env):
    script(env, mints=[CloudError("transfer_rate_limited", "slow down", 429,
                                  retry_after=120)])

    job = push(env)

    assert job["result"] == {"code": "transfer_rate_limited", "retry_after": 120}
    assert "2 minutes" in job["detail"]


@pytest.mark.parametrize("status", ["started_ok", "crash_looping", "failed_to_start"])
def test_data_push_stops_and_restarts_any_project_not_stopped(env, status):
    env.repos.projects.set_status("shop", status)
    script(env, targets=(TARGETS, TARGETS), mints=[ticket()], opens=[OPENED], uploads=[None],
           statuses=[DONE])

    # The dialog's "stops while its data is copied" notice reads the same flag.
    assert env.browser.get("/api/projects/shop/push").json()["running"] is True
    job = push(env)

    words = [" ".join(argv) for argv in env.runner.calls]
    down = next(i for i, w in enumerate(words) if " down" in w)
    pack = next(i for i, w in enumerate(words) if " run " in w and " pack " in w)
    up = next(i for i, w in enumerate(words) if w.endswith("up -d"))
    assert down < pack < up
    assert job["result"]["local_restart"] == "ok"


def test_a_volume_named_outside_the_project_fails_before_anything_stops(env):
    compose = COMPOSE_SHOP.replace("db-data: {}", "db-data: {name: blog_db-data}")
    (env.config.projects_root / "shop" / "docker-compose.yml").write_text(compose)
    env.repos.projects.set_status("shop", "started_ok")
    script(env)

    job = push(env)

    assert job["result"]["code"] == "bad_bundle"
    assert "blog_db-data" in job["detail"] and "shop_" in job["detail"]
    assert not [a for a in env.runner.calls if "down" in a or a[1] == "run"]
    assert env.instance.calls == []


def test_a_repeat_push_checks_volume_names_against_the_instance_project(env):
    # Without a compose name the instance names volumes after its own project id.
    compose = COMPOSE_SHOP.replace("db-data: {}", "db-data: {name: my-shop_db-data}")
    (env.config.projects_root / "shop" / "docker-compose.yml").write_text(compose)
    env.repos.instance_pushes.record("shop", "k1", console_hostname="k1.eggie.io",
                                    instance_project="my-shop", urls=[], secrets_missing=0,
                                    at=1792510000)
    script(env, mints=[ticket()], opens=[OPENED], uploads=[None], statuses=[DONE])

    assert push(env, replace_data=True)["state"] == "done"


def test_unregistered_project_fails_with_not_registered(env):
    env.repos.cloud_projects.unmap("shop")
    script(env)

    job = push(env)

    assert job["result"]["code"] == "not_registered"
    assert sum(env.sleeps) == 15


def test_sign_out_mid_push_fails_with_not_signed_in(env):
    pack = env.runner.on_docker_run

    def pack_then_sign_out(argv):
        env.repos.account.update(access_token=None)
        return pack(argv)

    env.runner.on_docker_run = pack_then_sign_out
    script(env, mints=[ticket()])

    job = push(env)

    assert job["result"]["code"] == "not_signed_in"


def test_project_lock_is_held_for_the_whole_push(env):
    uploading, release = threading.Event(), threading.Event()

    def upload(*_args):
        uploading.set()
        assert release.wait(5)

    script(env, mints=[ticket()], opens=[OPENED], uploads=[upload], statuses=[DONE])
    resp = env.browser.post("/api/projects/shop/push", json={"instance_id": "k1"})
    assert uploading.wait(5)

    busy = env.browser.post("/api/projects/shop/up")
    release.set()
    env.jobs.wait(resp.json()["job_id"], timeout=5)

    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "project_busy"


def test_a_failed_push_frees_the_project(env):
    script(env, mints=[CloudError("instance_forbidden", "no", 403)])

    assert push(env)["state"] == "failed"
    assert env.browser.post("/api/projects/shop/up").status_code == 202


def test_push_routes_are_console_only(env):
    assert env.client.get("/projects/shop/push").status_code == 404
    assert env.client.post("/projects/shop/push", json={"instance_id": "k1"}).status_code == 404


def test_staging_file_is_removed_after_the_push(env):
    staged = []
    script(env, mints=[ticket()], opens=[OPENED],
           uploads=[lambda host, push_id, token, path: staged.append(path.exists())],
           statuses=[DONE])

    assert push(env)["state"] == "done"
    assert staged == [True]
    assert list((env.config.transfers_root / "out").iterdir()) == []


def test_volume_sizes_read_dockers_decimal_units():
    class Runner:
        def exec(self, argv, *, root=False, env=None):
            return Completed(0, json.dumps({"Volumes": [
                {"Name": "shop_db-data", "Size": "52.43MB"}, {"Name": "empty", "Size": "0B"},
                {"Name": "big", "Size": "1.5GB"}, {"Name": "odd", "Size": "N/A"}]}), "")

    assert docker.volume_sizes(Runner()) == {"shop_db-data": 52_430_000, "empty": 0,
                                             "big": 1_500_000_000}
