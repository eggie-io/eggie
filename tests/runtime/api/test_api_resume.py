from eggie_api.domain.project import STARTED_OK
from tests.runtime.api.conftest import COMPOSE_MALFORMED, _create, _write_compose


def _up_dirs(env):
    return [a[3] for a in env.runner.calls if a[-2:] == ["up", "-d"]]


def _resume_and_wait(env):
    for job_id in env.app.state.resume_projects():
        env.jobs.wait(job_id, timeout=5)


def test_resume_starts_only_projects_left_running(env):
    # After a VM reboot the containers are gone but state.db still says
    # started; only those projects may come back, never a stopped one.
    for pid in ("blog", "shop"):
        _create(env, pid)
        _write_compose(env, pid)
    env.state.set_status("blog", STARTED_OK)
    env.state.set_status("shop", "stopped")

    _resume_and_wait(env)

    started = _up_dirs(env)
    assert len(started) == 1
    assert "/blog/" in started[0]


def test_one_unreadable_project_does_not_stop_the_others_resuming(env):
    for pid in ("broken", "blog"):
        _create(env, pid)
        env.state.set_status(pid, STARTED_OK)
    _write_compose(env, "broken", COMPOSE_MALFORMED)
    _write_compose(env, "blog")

    _resume_and_wait(env)

    started = _up_dirs(env)
    assert len(started) == 1
    assert "/blog/" in started[0]
