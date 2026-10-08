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


@pytest.mark.parametrize("argv", [
    ("API_KEY", "leaked"),
    ("API_KEY=leaked",),
    ("--value=leaked", "API_KEY"),
    ("API_KEY", "--value=leaked"),
])
def test_secret_set_never_takes_or_echoes_the_value_from_the_command_line(guest, capsys, argv):
    folder = _project(guest)
    try:
        code, out, err = guest.run("secret", "set", *argv, cwd=folder, stdin="piped")
    except SystemExit as e:
        code, out, err = e.code, "", ""
    captured = capsys.readouterr()
    assert code != 0
    assert "leaked" not in out + err + captured.out + captured.err
    assert _values(guest) == {}


def test_secret_request_and_rm_do_not_echo_a_malformed_name(guest):
    folder = _project(guest)
    for cmd in (("request", "K=leaked", "hint"), ("rm", "K=leaked")):
        code, out, err = guest.run("secret", *cmd, cwd=folder)
        assert code == 1 and "leaked" not in out + err


def test_secret_request_in_a_folder_the_api_does_not_know_yet(guest):
    folder = guest.root / "fresh"
    folder.mkdir()
    code, _, err = guest.run("secret", "request", "STRIPE_KEY", "hint", cwd=folder)
    assert (code, err) == (0, "")


def test_secret_request_for_a_name_with_a_value_does_not_open_a_request(guest):
    folder = _project(guest)
    guest.run("secret", "set", "API_KEY", cwd=folder, stdin="v")
    code, out, _ = guest.run("secret", "request", "API_KEY", "hint", cwd=folder)
    assert code == 0 and "already has a value" in out
    _, out, _ = guest.run("secret", "list", cwd=folder)
    assert "Requested:" not in out


def test_secret_set_reports_the_apis_validation_message(guest):
    folder = _project(guest)
    code, _, err = guest.run("secret", "set", "DOCKER_HOST", cwd=folder, stdin="x")
    assert code == 1
    assert "reserved" in err


def test_secret_list_shows_names_and_requests_but_no_values(guest):
    folder = _project(guest)
    guest.run("secret", "set", "API_KEY", cwd=folder, stdin="hidden-value")
    guest.run("secret", "request", "OTHER", "from the vendor", cwd=folder)
    code, out, _ = guest.run("secret", "list", cwd=folder)
    assert code == 0
    assert "API_KEY" in out
    assert "Requested:" in out and "OTHER — from the vendor" in out
    assert "hidden-value" not in out


def test_secret_request_lists_the_name_and_hint_until_it_is_set(guest):
    folder = _project(guest)
    code, out, err = guest.run("secret", "request", "STRIPE_KEY",
                               "Stripe → Developers → API keys", cwd=folder)
    assert (code, err) == (0, "")
    _, out, _ = guest.run("secret", "list", cwd=folder)
    assert "Requested:" in out and "STRIPE_KEY — Stripe → Developers → API keys" in out
    guest.run("secret", "set", "STRIPE_KEY", cwd=folder, stdin="sk\n")
    _, out, _ = guest.run("secret", "list", cwd=folder)
    assert "Requested:" not in out and "sk" not in out.split()


def test_secret_rm_dismisses_a_request(guest):
    folder = _project(guest)
    guest.run("secret", "request", "STRIPE_KEY", "hint", cwd=folder)
    code, out, _ = guest.run("secret", "rm", "STRIPE_KEY", cwd=folder)
    assert code == 0
    assert "Dismissed the request" in out
    _, out, _ = guest.run("secret", "list", cwd=folder)
    assert "STRIPE_KEY" not in out


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


def test_the_cli_refuses_the_same_reserved_names_as_the_api():
    from eggie_api.domain.secrets import RESERVED_NAMES, RESERVED_PREFIXES
    assert cli._RESERVED_NAMES == RESERVED_NAMES
    assert cli._RESERVED_PREFIXES == RESERVED_PREFIXES


def test_secret_set_refuses_a_reserved_name_before_reading_the_value(guest):
    folder = _project(guest)
    code, _, err = guest.run("secret", "set", "docker_host", cwd=folder)
    assert code == 1 and "reserved" in err
