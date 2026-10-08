import io

import pytest

from tests.runtime.api.conftest import COMPOSE_ONE_WEB
from tests.runtime.cli.loader import load

cli = load()


def _project(guest, name="blog"):
    folder = guest.root / name
    folder.mkdir()
    (folder / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
    guest.run("up", cwd=folder)
    return folder


def _values(guest, pid="blog"):
    return guest._client.app.state.state.secret_values(pid)


def test_secret_set_reads_the_value_from_stdin_without_its_trailing_newline(guest):
    folder = _project(guest)
    code, out, err = guest.run("secret", "set", "API_KEY", cwd=folder, stdin="abc\n")
    assert (code, err) == (0, "")
    assert _values(guest) == {"API_KEY": "abc"}
    assert "abc" not in out


def test_secret_set_keeps_inner_newlines_of_a_piped_value(guest):
    folder = _project(guest)
    guest.run("secret", "set", "PEM", cwd=folder, stdin="a\nb\n")
    assert _values(guest) == {"PEM": "a\nb"}


def test_secret_set_on_a_terminal_asks_without_echo(guest):
    folder = _project(guest)

    class Tty(io.StringIO):
        def isatty(self):
            return True

    prompts = []
    code, out, _ = guest.run("secret", "set", "API_KEY", cwd=folder, stdin=Tty(),
                             getpass=lambda p: prompts.append(p) or "typed")
    assert code == 0
    assert prompts and "API_KEY" in prompts[0]
    assert _values(guest) == {"API_KEY": "typed"}
    assert "typed" not in out


def test_secret_set_refuses_an_empty_value(guest):
    folder = _project(guest)
    code, _, err = guest.run("secret", "set", "API_KEY", cwd=folder, stdin="")
    assert code == 1
    assert "nothing was saved" in err
    assert _values(guest) == {}


def test_secret_set_never_takes_the_value_as_an_argument(guest):
    folder = _project(guest)
    with pytest.raises(SystemExit):
        guest.run("secret", "set", "API_KEY", "leaked", cwd=folder)


def test_secret_set_reports_the_apis_validation_message(guest):
    folder = _project(guest)
    code, _, err = guest.run("secret", "set", "DOCKER_HOST", cwd=folder, stdin="x")
    assert code == 1
    assert "reserved" in err


def test_secret_list_shows_names_and_missing_but_no_values(guest):
    folder = _project(guest)
    (folder / ".env.example").write_text("API_KEY=\nOTHER=\n")
    guest.run("secret", "set", "API_KEY", cwd=folder, stdin="hidden-value")
    code, out, _ = guest.run("secret", "list", cwd=folder)
    assert code == 0
    assert "API_KEY" in out
    assert "OTHER" in out and "missing" in out.lower()
    assert "hidden-value" not in out


def test_secret_set_on_a_running_project_says_how_to_apply_it(guest):
    folder = _project(guest)
    _, out, _ = guest.run("secret", "set", "API_KEY", cwd=folder, stdin="v")
    assert "eggie up" in out


def test_secret_rm_removes_it_and_an_unknown_name_fails(guest):
    folder = _project(guest)
    guest.run("secret", "set", "API_KEY", cwd=folder, stdin="v")
    assert guest.run("secret", "rm", "API_KEY", cwd=folder)[0] == 0
    assert _values(guest) == {}
    code, _, err = guest.run("secret", "rm", "API_KEY", cwd=folder)
    assert code == 1 and "API_KEY" in err
