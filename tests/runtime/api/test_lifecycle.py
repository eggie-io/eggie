from eggie_api.core import lifecycle
from eggie_api.core.lifecycle import compose_up, _compose_argv
from eggie_api.core.project import Project, STARTED_OK
from eggie_api.core.detect import WebSpec
from eggie_api.core.exec import Completed

DIR = "/srv/projects/myproj"


class FakeProvider:
    def __init__(self):
        self.execs = []

    def exec(self, argv, *, root=False):
        self.execs.append(argv)
        joined = " ".join(argv)
        if "ps" in argv and "--format" in joined:
            return Completed(0, '[{"Service":"web","State":"running","ExitCode":0}]', "")
        return Completed(0, "", "")


def test_compose_argv_uses_both_files_in_order():
    # The directory comes from the caller, never from a constant: the API
    # uploads to config.projects_root, and a second source of truth here would
    # run compose against a directory nothing was ever written to.
    argv = _compose_argv(DIR)
    d = DIR
    assert argv[:2] == ["/usr/bin/docker", "compose"]
    assert argv.count("-f") == 2
    assert f"{d}/docker-compose.yml" in argv
    assert f"{d}/.eggie/overlay.yml" in argv
    assert argv[-2:] == ["up", "-d"]


def test_compose_up_runs_against_the_directory_it_is_given(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    proj = Project(id="myproj", webs=[WebSpec("web", 80)])
    p = FakeProvider()
    status, detail = compose_up(p, proj, tmp_path, "d.io")
    assert status == STARTED_OK
    assert detail == "", "a healthy stack reports no failure detail"
    # overlay was written into the guest, compose up ran with both -f files,
    # all of them under the directory the caller passed.
    assert any("overlay.yml" in " ".join(a) for a in p.execs)
    assert any(a[-2:] == ["up", "-d"] for a in p.execs)
    for argv in p.execs:
        for word in argv:
            if "docker-compose.yml" in word or "overlay.yml" in word:
                assert str(tmp_path) in word, word


def test_compose_up_carries_the_guest_error_when_the_stack_fails():
    # A bare status like "failed_to_start" is unactionable: the reason lives in
    # compose's own stderr, which used to be discarded.
    from eggie_api.core.exec import Completed
    from eggie_api.core.project import FAILED_TO_START

    class FailingProvider(FakeProvider):
        def exec(self, argv, *, root=False):
            self.execs.append(argv)
            if argv[-2:] == ["up", "-d"]:
                return Completed(1, "", "network edge declared as external, but could not be found")
            return Completed(0, "[]", "")

    proj = Project(id="myproj", webs=[WebSpec("web", 80)])
    status, detail = compose_up(FailingProvider(), proj, DIR, "d.io")
    assert status == FAILED_TO_START
    assert "network edge" in detail


def test_compose_up_reports_a_failed_overlay_write_instead_of_starting_the_stack():
    # exec() never raises, so an unchecked overlay write turns into a project
    # that comes up with no Traefik labels: no route, and nothing anywhere
    # saying why. The write has to be the thing that fails, loudly.
    from eggie_api.core.exec import Completed
    from eggie_api.core.project import FAILED_TO_START

    class OverlayFails(FakeProvider):
        def exec(self, argv, *, root=False):
            self.execs.append(argv)
            if "overlay.yml" in " ".join(argv):
                return Completed(1, "", "bash: /opt/eggie/projects/myproj: Permission denied")
            return Completed(0, "[]", "")

    p = OverlayFails()
    proj = Project(id="myproj", webs=[WebSpec("web", 80)])
    status, detail = compose_up(p, proj, DIR, "d.io")

    assert status == FAILED_TO_START
    assert "Permission denied" in detail
    assert not any(a[-2:] == ["up", "-d"] for a in p.execs), \
        "compose must not start a stack whose overlay was never written"


def test_resolve_compose_name_prefers_what_the_containers_carry():
    # A compose file's `name:` can change after the last start; delete must
    # find what is actually running, not what the file says today.
    class Runner:
        def exec(self, argv, *, root=False):
            if argv[1:3] == ["ps", "-a"] and any("working_dir" in a for a in argv):
                return Completed(0, "fancy\n", "")
            return Completed(0, "", "")

    name = lifecycle.resolve_compose_name(Runner(), "/opt/eggie/projects/blog", "blog")
    assert name == "fancy"


def test_resolve_compose_name_falls_back_when_no_container_carries_the_label():
    class Runner:
        def exec(self, argv, *, root=False):
            return Completed(0, "", "")

    name = lifecycle.resolve_compose_name(Runner(), "/opt/eggie/projects/blog", "stored")
    assert name == "stored"


def test_resolve_compose_name_falls_back_when_the_lookup_command_fails():
    class Runner:
        def exec(self, argv, *, root=False):
            return Completed(1, "", "daemon not running")

    name = lifecycle.resolve_compose_name(Runner(), "/opt/eggie/projects/blog", "stored")
    assert name == "stored"


def test_remove_by_label_stops_containers_before_removing_them():
    calls = []

    class Runner:
        def exec(self, argv, *, root=False):
            calls.append(argv)
            if argv[1:3] == ["ps", "-a"]:
                return Completed(0, "c1\nc2\n", "")
            return Completed(0, "", "")

    result = lifecycle.remove_by_label(Runner(), "blog", volumes=False)
    assert result.ok
    docker_calls = [a for a in calls if a[0] == lifecycle.DOCKER]
    stop = next(a for a in docker_calls if a[1] == "stop")
    rm = next(a for a in docker_calls if a[1:3] == ["rm", "-f"])
    assert stop[2:] == ["c1", "c2"]
    assert rm[3:] == ["c1", "c2"]
    assert docker_calls.index(stop) < docker_calls.index(rm)


def test_remove_by_label_attempts_every_step_and_returns_the_first_failure():
    calls = []

    class Runner:
        def exec(self, argv, *, root=False):
            calls.append(argv)
            if argv[1:3] == ["network", "ls"]:
                return Completed(0, "net1\n", "")
            if argv[1:3] == ["network", "rm"]:
                return Completed(1, "", "network busy")
            if argv[1:3] == ["volume", "ls"]:
                return Completed(0, "vol1\n", "")
            return Completed(0, "", "")

    result = lifecycle.remove_by_label(Runner(), "blog", volumes=True)
    assert not result.ok
    assert result.stderr == "network busy"
    # The network step failed, but the volume step -- requested via
    # volumes=True -- still ran instead of being skipped after the failure.
    assert any(a[1:4] == ["volume", "rm", "-f"] for a in calls)
