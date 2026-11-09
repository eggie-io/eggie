import json
import os

import pytest

from eggie_api.errors import Conflict
from eggie_api.services.runtime_update import RuntimeUpdate
from tests.runtime.api.test_api_public import console

NOW = 1_000_000


class Dirs:
    def __init__(self, tmp_path, installed="runtime-v0.1.0\n"):
        self.dir = tmp_path / "update"
        self.dir.mkdir()
        self.marker = tmp_path / "runtime.version"
        if installed is not None:
            self.marker.write_text(installed)
        self.service = RuntimeUpdate(self.dir, self.marker, clock=lambda: NOW)

    def write(self, name, content, age=0):
        path = self.dir / name
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        os.utime(path, (NOW - age, NOW - age))

    def available(self, tag="runtime-v0.2.0"):
        self.write("available.json", {"available": tag, "checked_at": 1})

    def run_status(self, state, request=1, error=None, age=0):
        self.write("status.json", {"request": request, "state": state, "error": error}, age)


@pytest.fixture
def d(tmp_path):
    return Dirs(tmp_path)


def test_a_newer_release_is_offered_without_the_tag_prefix(d):
    d.available()

    assert d.service.status() == {"installed": "0.1.0", "available": "0.2.0",
                                  "state": "idle", "error": None}


@pytest.mark.parametrize("installed, available", [
    ("runtime-v0.1.0\n", "runtime-v0.1.0"),
    ("runtime-v0.10.0\n", "runtime-v0.9.0"),
    ("main\n", "runtime-v0.2.0"),
])
def test_a_release_that_is_not_newer_than_the_installed_one_is_not_offered(tmp_path, installed, available):
    d = Dirs(tmp_path, installed)
    d.available(available)

    assert d.service.status()["available"] is None


def test_missing_or_malformed_files_mean_nothing_to_offer(tmp_path):
    d = Dirs(tmp_path, installed=None)
    assert d.service.status() == {"installed": None, "available": None,
                                  "state": "idle", "error": None}
    d.write("available.json", "{not json")
    d.write("status.json", "[]")
    d.write("request", "x")
    assert d.service.status()["state"] == "idle"


def test_a_request_not_yet_picked_up_is_running(d):
    d.run_status("done", request=4)
    d.write("request", "5", age=30)

    assert d.service.status()["state"] == "running"


def test_a_request_not_picked_up_for_two_minutes_has_failed(d):
    d.run_status("done", request=4)
    d.write("request", "5", age=121)

    assert d.service.status() == {"installed": "0.1.0", "available": None, "state": "failed",
                                  "error": "The updater inside Eggie did not start."}


def test_an_update_running_for_half_an_hour_has_failed(d):
    d.write("request", "1")
    d.run_status("running", age=1801)

    assert d.service.status()["error"] == "The update did not finish."


def test_an_update_running_for_a_while_is_still_running(d):
    d.write("request", "1")
    d.run_status("running", age=1700)

    assert d.service.status()["state"] == "running"


def test_a_failed_update_carries_its_error(d):
    d.write("request", "1")
    d.run_status("failed", error="docker pull failed")

    assert (d.service.status()["state"], d.service.status()["error"]) == ("failed", "docker pull failed")


def test_requesting_bumps_the_counter_once_and_reports_running(d):
    d.available()
    d.write("request", "4")
    d.run_status("done", request=4)

    out = d.service.request()

    assert (d.dir / "request").read_text() == "5"
    assert out["state"] == "running"


def test_requesting_while_running_is_refused(d):
    d.available()
    d.write("request", "1")
    d.run_status("running")

    with pytest.raises(Conflict) as e:
        d.service.request()

    assert e.value.code == "update_running"
    assert (d.dir / "request").read_text() == "1"


def test_requesting_with_nothing_newer_is_refused(d):
    with pytest.raises(Conflict) as e:
        d.service.request()

    assert e.value.code == "update_not_available"
    assert not (d.dir / "request").exists()


def test_the_routes_are_console_only(env):
    assert console(env.app, env.client).get("/api/runtime/update").status_code == 200
    assert env.client.get("/runtime/update").status_code == 404
