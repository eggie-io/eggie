import os

import pytest

from tests.runtime.api.conftest import COMPOSE_ONE_WEB
from tests.runtime.cli.loader import load

cli = load()


def test_new_creates_a_folder_named_by_the_project_id(guest):
    code, out, _err = guest.run("new", "Routine Tracker", cwd=guest.root)
    assert code == 0
    assert (guest.root / "routine-tracker").is_dir()
    assert str(guest.root / "routine-tracker") in out


@pytest.mark.parametrize("url,folder", [
    ("https://github.com/org/My.Repo.git", "my-repo"),
    ("https://github.com/org/blog/", "blog"),
    ("git@github.com:org/Shop.git", "shop"),
])
def test_clone_names_the_folder_after_the_repository(guest, url, folder):
    code, _out, err = guest.run("clone", url, cwd=guest.root)
    assert (code, err) == (0, "")
    assert (guest.root / folder).is_dir()


def test_clone_never_lets_git_wait_for_a_password(guest):
    # A coding agent's shell has no terminal: a credential prompt hangs forever.
    guest.git_result = (128, "fatal: could not read Username for "
                             "'https://github.com': terminal prompts disabled")
    code, _out, err = guest.run("clone", "https://github.com/org/private.git",
                                cwd=guest.root)
    _argv, env = guest.git_calls[0]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert code == 1
    assert "Could not download https://github.com/org/private.git" in err
    assert "terminal prompts disabled" in err


def test_a_cloned_repo_with_a_compose_file_is_started_straight_away(guest):
    guest.clone_files = {"docker-compose.yml": COMPOSE_ONE_WEB}
    code, out, err = guest.run("clone", "https://github.com/org/blog.git",
                               cwd=guest.root)
    assert (code, err) == (0, "")
    assert "http://blog.test.local:41080" in out


def test_a_cloned_repo_with_a_differently_named_compose_file_is_named_not_reported_missing(guest):
    guest.clone_files = {"compose.yaml": COMPOSE_ONE_WEB}
    code, out, err = guest.run("clone", "https://github.com/org/blog.git",
                               cwd=guest.root)
    assert code == 1
    assert "compose.yaml" in err
    assert "Eggie reads only docker-compose.yml" in err
    assert "no docker-compose.yml yet" not in out


def test_a_cloned_repo_without_a_compose_file_says_one_is_needed(guest):
    code, out, _err = guest.run("clone", "https://github.com/org/notes.git",
                                cwd=guest.root)
    assert code == 0
    assert "no docker-compose.yml yet" in out


def test_new_and_clone_never_reuse_an_existing_folder(guest):
    (guest.root / "blog").mkdir()
    assert guest.run("new", "Blog", cwd=guest.root)[0] == 1
    assert guest.run("clone", "https://github.com/org/blog.git", cwd=guest.root)[0] == 1
    assert guest.git_calls == []


def _deny_api():
    raise cli.EggieError(
        "This user can't reach Eggie: it must be in the docker group. "
        "Run `sudo usermod -aG docker $USER` and start a new session.")


def test_new_creates_nothing_when_this_user_cannot_reach_the_api(guest):
    guest._api_client = _deny_api
    code, _out, err = guest.run("new", "Blog", cwd=guest.root)
    assert code == 1
    assert "docker group" in err
    assert not (guest.root / "blog").exists()


def test_clone_never_calls_git_when_this_user_cannot_reach_the_api(guest):
    guest._api_client = _deny_api
    code, _out, err = guest.run("clone", "https://github.com/org/blog.git", cwd=guest.root)
    assert code == 1
    assert "docker group" in err
    assert guest.git_calls == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write through any permission")
def test_new_on_an_unwritable_projects_root_says_so_without_a_traceback(guest):
    guest.root.chmod(0o500)
    try:
        code, _out, err = guest.run("new", "Blog", cwd=guest.root)
    finally:
        guest.root.chmod(0o700)
    assert code == 1
    assert f"Eggie could not create {guest.root / 'blog'}" in err
    assert "Traceback" not in err
