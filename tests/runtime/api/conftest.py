import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from omelet_api.routes.app import create_app
from omelet_api.core.config import ApiConfig
from omelet_api.core.exec import Completed
from omelet_api.core.state import State

BROWSER = {"Host": "localhost:41080"}

PS_RUNNING = '[{"Service":"web","State":"running","ExitCode":0}]'
# One LISTEN row (st 0A) on 127.0.0.1:80, the shape `cat /proc/net/tcp` prints.
PROC_NET_LOOPBACK = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when\n"
    "   0: 0100007F:0050 00000000:0000 0A 00000000:00000000 00:00000000\n")
# NDJSON: docker compose emits one object per line on some versions.
PS_RESTARTING = ('{"Service":"web","State":"restarting","ExitCode":1}\n'
                 '{"Service":"db","State":"running","ExitCode":0}')

COMPOSE_ONE_WEB = """
services:
  web:
    image: nginx
    ports: ["8080:80"]
"""
COMPOSE_MALFORMED = "services:\n  web:\n   image: nginx\n    ports: bad\n"
COMPOSE_AMBIGUOUS = """
services:
  api:
    image: api
  worker:
    image: worker
"""


class FakeRunner:
    """Records argv and replays scripted results; never spawns a process."""

    def __init__(self):
        self.calls = []
        self.docker_version = Completed(0, "27.1.1", "")
        self.up = Completed(0, "", "")
        self.ps = Completed(0, PS_RUNNING, "")
        self.down = Completed(0, "", "")
        self.logs = Completed(0, "web-1 | listening on 80\n", "")
        self.stream_lines = ["web-1 | one\n", "web-1 | two\n"]
        self.container_id = Completed(0, "c0ffee1234\n", "")
        self.proc_net = Completed(0, PROC_NET_LOOPBACK, "")
        # Empty by default: delete falls back to the stored/id name, same as
        # before this lookup existed, unless a test scripts a real answer.
        self.compose_name_lookup = Completed(0, "", "")
        self.up_gate = None
        self.envs = []
        self.clone = Completed(0, "", "")
        # Called with the destination path before `clone` is returned, so a
        # test can put files where a real clone would.
        self.on_clone = None

    def exec(self, argv, *, root=False, env=None):
        self.calls.append(argv)
        self.envs.append(env)
        if argv[0] == "sh" and "clone" in argv:
            if self.on_clone is not None:
                self.on_clone(argv[-1])
            return self._reply(self.clone)
        if argv[1] == "version":
            return self._reply(self.docker_version)
        if argv[1] == "exec":
            return self.proc_net
        if "-q" in argv:
            return self.container_id
        if argv[-2:] == ["up", "-d"]:
            if self.up_gate is not None:
                assert self.up_gate.wait(5), "the up job was never released"
            return self.up
        if argv[1:3] == ["ps", "-a"] and any("working_dir" in a for a in argv):
            return self.compose_name_lookup
        if "ps" in argv:
            return self.ps
        if "down" in argv:
            return self.down
        if "logs" in argv:
            return self.logs
        return Completed(0, "", "")

    @staticmethod
    def _reply(scripted):
        if isinstance(scripted, Exception):
            raise scripted
        return scripted

    def stream(self, argv, *, root=False):
        self.calls.append(argv)
        return iter(list(self.stream_lines))

    def argv_containing(self, needle):
        # Matched against the argv words, not the joined string: project paths
        # are real directories here, and a tmp_path can contain any substring.
        return [a for a in self.calls if needle in a]


class FakeProbe:
    """Answers the health probe without a socket. 200 by default, so an
    ordinary `up` in these tests never enters the retry window."""

    def __init__(self):
        self.status = 200
        self.calls = []

    def __call__(self, url, host):
        self.calls.append((url, host))
        return self.status


AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def env(tmp_path):
    token_path = tmp_path / "api.token"
    token_path.write_text("test-token")
    config = ApiConfig(
        domain="test.local",
        edge_port=41080,
        projects_root=tmp_path / "projects",
        state_db=tmp_path / "state.db",
        version="9.9.9",
        token_path=token_path,
        connect_path=tmp_path / "connect.json",
        uploads_root=tmp_path / "uploads",
        # No retry window: these tests assert on the verdict, and the window
        # itself is covered against a fake clock in test_health.py.
        ready_timeout=0.0,
        stack_file=tmp_path / "stack.yml",
        tunnel_token_path=tmp_path / "tunnel.token",
    )
    runner = FakeRunner()
    probe = FakeProbe()
    app = create_app(config=config, runner=runner, http_probe=probe)
    with TestClient(app, headers=AUTH) as client:
        # raw_client returns the 500 a real caller would see instead of
        # re-raising the exception inside the test.
        yield SimpleNamespace(client=client, config=config, runner=runner,
                              probe=probe, state=app.state.state, jobs=app.state.jobs,
                              raw_client=TestClient(app, raise_server_exceptions=False,
                                                    headers=AUTH),
                              app=app)


def _create(env, pid="blog", **body):
    return env.client.post("/projects", json={"id": pid, **body})


def _write_compose(env, pid, text=COMPOSE_ONE_WEB):
    (env.config.projects_root / pid).mkdir(parents=True, exist_ok=True)
    (env.config.projects_root / pid / "docker-compose.yml").write_text(text)


def _run_to_completion(env, resp):
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    env.jobs.wait(job_id, timeout=5)
    return env.client.get(f"/jobs/{job_id}").json()
