"""The project commands, driven against a fake API client.

Every command now talks HTTP to the API, so these assert on the calls the
CLI makes and on what a user is told when the API refuses or a job fails --
not on any local compose or state handling, which no longer exists on the host.
"""
from typer.testing import CliRunner

import host.cli as cli
from host.client import (ApiError, ApiUnavailableError, JobFailedError)

runner = CliRunner()


class FakeClient:
    def __init__(self, *, job=None, projects=None, delete=None, logs="",
                 fail=None):
        self.calls: list[tuple] = []
        self._job = job or {"state": "done",
                            "result": {"status": "started_ok",
                                       "urls": ["http://blog.local.eggie.space:39080"]}}
        self._projects = projects or []
        self._delete = delete or {"id": "blog", "stopped": True, "detail": ""}
        self._logs = logs
        self._fail = fail

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if self._fail is not None and name in self._fail:
            raise self._fail[name]

    def ensure_project(self, project_id, **kw):
        self._record("ensure_project", project_id)
        return {"id": project_id, "urls": [], "problem": None}

    def upload_directory(self, project_id, local_dir):
        self._record("upload_directory", project_id, str(local_dir))
        return {"id": project_id, "files": []}

    def project_up(self, project_id):
        self._record("project_up", project_id)
        return "job-1"

    def project_down(self, project_id):
        self._record("project_down", project_id)
        return "job-2"

    def wait_for_job(self, job_id, **kw):
        self._record("wait_for_job", job_id)
        return self._job

    def list_projects(self):
        self._record("list_projects")
        return self._projects

    def delete_project(self, project_id):
        self._record("delete_project", project_id)
        return self._delete

    def logs(self, project_id, service=None):
        self._record("logs", project_id, service)
        return self._logs


def use(monkeypatch, client):
    monkeypatch.setattr(cli, "_client_factory", lambda: client)
    return client


def project_dir(tmp_path, name="blog"):
    d = tmp_path / name
    d.mkdir()
    (d / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx\n")
    return d


def test_up_creates_uploads_starts_and_prints_the_url(monkeypatch, tmp_path):
    client = use(monkeypatch, FakeClient())
    d = project_dir(tmp_path)
    result = runner.invoke(cli.app, ["up", str(d)])
    assert result.exit_code == 0, result.output
    assert [c[0] for c in client.calls] == [
        "ensure_project", "upload_directory", "project_up", "wait_for_job"]
    assert client.calls[0][1] == "blog"
    assert "http://blog.local.eggie.space:39080" in result.output


def test_up_says_so_when_a_project_exposes_nothing_over_http(monkeypatch, tmp_path):
    # A worker-only project starts fine and has no URL; printing nothing at
    # all leaves the user unable to tell success from a no-op.
    use(monkeypatch, FakeClient(job={"state": "done",
                                     "result": {"status": "started_ok", "urls": []}}))
    result = runner.invoke(cli.app, ["up", str(project_dir(tmp_path))])
    assert result.exit_code == 0
    assert "started" in result.output and "no service is exposed" in result.output.lower()


def test_up_reports_the_guests_own_output_when_the_stack_does_not_stay_up(
        monkeypatch, tmp_path):
    failure = JobFailedError("web exited with code 1: bind: address in use",
                             {"status": "crash_looping", "urls": []})
    client = use(monkeypatch, FakeClient(fail={"wait_for_job": failure}))
    result = runner.invoke(cli.app, ["up", str(project_dir(tmp_path))])
    assert result.exit_code == 1
    assert "did not stay running" in result.output, \
        "a bare status code is not a sentence anyone can act on"
    assert "crash_looping" in result.output, "support still needs the raw status"
    assert "bind: address in use" in result.output
    assert "eggie logs blog" in result.output


def test_up_without_a_compose_file_imports_and_says_so(monkeypatch, tmp_path):
    # The folder still lands in the VM; the coding agent writes the compose
    # file later. Refusing here would refuse exactly the folders Import
    # exists for.
    client = use(monkeypatch, FakeClient())
    empty = tmp_path / "nothing"
    empty.mkdir()
    result = runner.invoke(cli.app, ["up", str(empty)])
    assert result.exit_code == 0
    assert "nothing" in result.output and "there is nothing to start" in result.output.lower()
    # The folder was still imported: ensure_project and upload_directory ran,
    # but project_up must not have been called since there is nothing to start.
    assert [c[0] for c in client.calls] == ["ensure_project", "upload_directory"]


def test_an_unreachable_api_is_reported_in_words_not_a_socket_error(
        monkeypatch, tmp_path):
    def boom():
        raise ApiUnavailableError("could not reach the Eggie API (refused). "
                                    "The VM may be stopped")
    monkeypatch.setattr(cli, "_client_factory", boom)
    result = runner.invoke(cli.app, ["up", str(project_dir(tmp_path))])
    assert result.exit_code == 1
    assert "The VM may be stopped" in result.output
    assert "Traceback" not in result.output


def test_status_lists_the_apis_projects_and_shows_a_broken_one(monkeypatch):
    use(monkeypatch, FakeClient(projects=[
        {"id": "blog", "status": "started_ok", "domain": "d",
         "urls": ["http://blog.d:39080"], "problem": None},
        {"id": "broken", "status": "stopped", "domain": "d", "urls": [],
         "problem": {"code": "invalid_compose",
                     "message": "docker-compose.yml is not valid YAML: line 3"}},
    ]))
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "http://blog.d:39080" in result.output
    # The honest answer to "why is my project not working" must be printed.
    assert "not valid YAML" in result.output


def test_status_with_no_projects(monkeypatch):
    use(monkeypatch, FakeClient(projects=[]))
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0 and "No projects." in result.output


def test_down_waits_for_the_job_before_claiming_the_project_is_stopped(monkeypatch):
    client = use(monkeypatch, FakeClient(job={"state": "done", "result": {}}))
    result = runner.invoke(cli.app, ["down", "blog"])
    assert result.exit_code == 0
    assert [c[0] for c in client.calls] == ["project_down", "wait_for_job"]
    assert "blog stopped." in result.output


def test_logs_reports_the_apis_message_when_compose_cannot_produce_them(
        monkeypatch):
    refusal = ApiError("logs_unavailable", "no such service: web", 409)
    use(monkeypatch, FakeClient(fail={"logs": refusal}))
    result = runner.invoke(cli.app, ["logs", "blog"])
    assert result.exit_code == 1
    assert "no such service: web" in result.output


def test_destroy_stays_loud_when_the_containers_could_not_be_stopped(monkeypatch):
    use(monkeypatch, FakeClient(delete={"id": "blog", "stopped": False,
                                        "detail": "dockerd is not running"}))
    result = runner.invoke(cli.app, ["destroy", "blog"])
    assert result.exit_code == 1
    assert "dockerd is not running" in result.output


def test_up_warns_when_the_api_could_not_reach_the_started_project(monkeypatch,
                                                                    tmp_path):
    # A URL printed with no warning is exactly the silent failure the API's
    # probe exists to end.
    client = use(monkeypatch, FakeClient(job={
        "state": "done",
        "result": {"status": "started_ok",
                   "urls": ["http://blog.local.eggie.space:39080"],
                   "problem": {"code": "bound_to_loopback",
                               "message": "listen on 0.0.0.0, not 127.0.0.1"}}}))
    result = runner.invoke(cli.app, ["up", str(project_dir(tmp_path))])

    assert result.exit_code == 0, "the containers did start"
    assert "listen on 0.0.0.0" in result.output
