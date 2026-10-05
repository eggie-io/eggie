import threading
import time

import pytest

from eggie_api.core.exec import Completed
from tests.runtime.api.conftest import COMPOSE_MALFORMED, COMPOSE_ONE_WEB
from tests.runtime.cli.loader import load

cli = load()


def _project(guest, name="blog", compose=COMPOSE_ONE_WEB):
    folder = guest.root / name
    folder.mkdir()
    (folder / "docker-compose.yml").write_text(compose)
    return folder


def _compose_ups(runner):
    return [argv for argv in runner.calls if argv[-2:] == ["up", "-d"]]


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition never became true"
        time.sleep(0.01)


def test_up_turns_a_folder_in_the_projects_root_into_a_routed_project(guest):
    folder = _project(guest)
    code, out, err = guest.run("up", cwd=folder)
    assert (code, err) == (0, "")
    assert "http://blog.test.local:41080" in out
    # Started with the generated overlay: without it there is no Traefik route.
    assert guest.runner.argv_containing(f"{folder}/.eggie/overlay.yml")


def test_up_from_a_subfolder_starts_the_enclosing_project(guest):
    folder = _project(guest)
    (folder / "src").mkdir()
    code, out, _err = guest.run("up", cwd=folder / "src")
    assert code == 0
    assert "http://blog.test.local:41080" in out


def test_a_second_up_restarts_an_already_registered_project(guest):
    folder = _project(guest)
    assert guest.run("up", cwd=folder)[0] == 0
    code, _out, err = guest.run("up", cwd=folder)
    assert (code, err) == (0, "")
    assert len(_compose_ups(guest.runner)) == 2


@pytest.mark.parametrize("alt_name", ["compose.yaml", "compose.yml", "docker-compose.yaml"])
def test_a_compose_file_under_another_name_is_named_not_reported_missing(guest, alt_name):
    folder = guest.root / "blog"
    folder.mkdir()
    (folder / alt_name).write_text(COMPOSE_ONE_WEB)
    code, _out, err = guest.run("up", cwd=folder)
    assert code == 1
    assert alt_name in err
    assert "Eggie reads only docker-compose.yml" in err


def test_a_broken_compose_file_is_reported_in_the_apis_own_words(guest):
    folder = _project(guest, compose=COMPOSE_MALFORMED)
    code, _out, err = guest.run("up", cwd=folder)
    assert code == 1
    assert "docker-compose.yml is not valid YAML" in err


def test_a_start_that_fails_reports_the_guest_output_and_points_at_logs(guest):
    guest.runner.up = Completed(1, "", "port is already allocated")
    folder = _project(guest)
    code, _out, err = guest.run("up", cwd=folder)
    assert code == 1
    assert "port is already allocated" in err
    assert "eggie logs" in err


def test_a_busy_project_is_waited_for_rather_than_reported(guest):
    folder = _project(guest)
    guest.runner.up_gate = threading.Event()
    first = threading.Thread(target=guest.run, args=("up",), kwargs={"cwd": folder})
    first.start()
    # Once compose up is running, the project's lock is held.
    _wait_for(lambda: _compose_ups(guest.runner))
    threading.Timer(0.2, guest.runner.up_gate.set).start()

    code, out, err = guest.run("down", cwd=folder)
    first.join(5)

    assert (code, err) == (0, "")
    assert "blog stopped." in out


def test_status_names_folders_that_are_not_set_up_yet(guest):
    blog = _project(guest)
    assert guest.run("up", cwd=blog)[0] == 0
    (guest.root / "shop").mkdir()
    code, out, _err = guest.run("status", cwd=guest.root)
    assert code == 0
    assert out.splitlines()[0].startswith("blog")
    assert "Not set up yet (run `eggie up` in each): shop" in out


def test_a_leftover_selftest_folder_is_never_listed_as_not_set_up(guest):
    # install.verify_step deletes the self-test project through the API but
    # leaves its folder behind; every fresh VM would otherwise offer it to the
    # first agent that runs `eggie status`.
    (guest.root / cli.VERIFY_PROJECT_ID).mkdir()
    code, out, _err = guest.run("status", cwd=guest.root)
    assert code == 0
    assert cli.VERIFY_PROJECT_ID not in out


def test_status_inside_an_unregistered_folder_says_how_to_set_it_up(guest):
    (guest.root / "shop").mkdir()
    code, out, _err = guest.run("status", cwd=guest.root / "shop")
    assert code == 0
    assert "shop is not set up yet" in out


def test_logs_and_down_outside_a_project_say_where_to_run_them(guest):
    for command in ("logs", "down"):
        code, _out, err = guest.run(command, cwd=guest.root)
        assert code == 1
        assert "inside a project folder" in err
        assert "~/projects (/opt/eggie/projects)" in err


def test_up_outside_the_projects_root_names_the_real_path_too(guest, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    code, _out, err = guest.run("up", cwd=outside)
    assert code == 1
    assert "~/projects (/opt/eggie/projects)" in err
