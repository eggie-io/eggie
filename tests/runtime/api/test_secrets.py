import pytest

from eggie_api.core.secrets import (SecretError, check_name, check_total,
                                    check_value, expected, parse_dotenv)


@pytest.mark.parametrize("name", ["API_KEY", "_x", "a1"])
def test_ordinary_env_names_are_accepted(name):
    check_name(name)


@pytest.mark.parametrize("name", ["", "1KEY", "MY-KEY", "A B", "KÉY"])
def test_names_compose_cannot_pass_as_env_are_refused(name):
    with pytest.raises(SecretError) as e:
        check_name(name)
    assert e.value.code == "secret_name_invalid"


@pytest.mark.parametrize("name", ["COMPOSE_PROJECT_NAME", "DOCKER_HOST", "compose_file"])
def test_names_that_steer_compose_itself_are_reserved(name):
    with pytest.raises(SecretError) as e:
        check_name(name)
    assert e.value.code == "secret_name_reserved"


def test_a_value_over_64_kib_is_refused_but_64_kib_is_not():
    check_value("x" * 65536)
    with pytest.raises(SecretError) as e:
        check_value("x" * 65537)
    assert e.value.code == "secret_too_large"


def test_a_value_is_measured_in_utf8_bytes():
    with pytest.raises(SecretError):
        check_value("é" * 32769)


def test_a_nul_byte_cannot_travel_through_an_environment():
    with pytest.raises(SecretError) as e:
        check_value("a\x00b")
    assert e.value.code == "secret_invalid_value"


def test_the_project_total_is_capped():
    check_total({f"K{i}": "x" * 60000 for i in range(8)})
    with pytest.raises(SecretError) as e:
        check_total({f"K{i}": "x" * 60000 for i in range(9)})
    assert e.value.code == "secrets_too_large"


def test_expected_reads_env_example_keys_and_ignores_their_values():
    example = "# comment\n\nexport API_KEY=placeholder\nDB_URL = postgres://x\nnot a line\n"
    assert expected(example, None) == {"API_KEY", "DB_URL"}


def test_expected_counts_compose_refs_without_a_default():
    compose = (
        "services:\n"
        "  web:\n"
        "    environment:\n"
        "      A: ${A}\n"
        "      B: ${B:-fallback}\n"
        "      C: ${C-fallback}\n"
        "      D: ${D:?must be set}\n"
        "      E: $E\n"
        "      F: $${F}\n"
        "      G: ${G:+on}\n"
        "      H: $$$H\n"
        "    # I: ${I}\n"
    )
    assert expected(None, compose) == {"A", "D", "E", "H"}


def test_expected_with_nothing_to_read_is_empty():
    assert expected(None, None) == set()


def test_parse_dotenv_handles_quotes_export_and_inline_comments():
    text = (
        "# top\n"
        "export PLAIN=abc  # trailing\n"
        "SINGLE='a $b # not a comment'\n"
        'DOUBLE="line1\\nline2 \\"q\\" \\\\"\n'
        "EMPTY=\n"
        "HASH=a#b\n"
    )
    assert parse_dotenv(text) == {
        "PLAIN": "abc",
        "SINGLE": "a $b # not a comment",
        "DOUBLE": 'line1\nline2 "q" \\',
        "EMPTY": "",
        "HASH": "a#b",
    }


def test_parse_dotenv_reads_a_double_quoted_value_across_lines():
    text = 'KEY="-----BEGIN-----\nabc\n-----END-----"\nNEXT=1\n'
    assert parse_dotenv(text) == {"KEY": "-----BEGIN-----\nabc\n-----END-----",
                                  "NEXT": "1"}


def test_parse_dotenv_refuses_a_line_without_an_equals_sign():
    with pytest.raises(SecretError) as e:
        parse_dotenv("GOOD=1\njust words\n")
    assert e.value.code == "dotenv_invalid"
    assert "line 2" in e.value.message


def test_parse_dotenv_refuses_an_unclosed_quote():
    with pytest.raises(SecretError) as e:
        parse_dotenv('KEY="never closed\n')
    assert e.value.code == "dotenv_invalid"


def test_parse_dotenv_refuses_a_reserved_name():
    with pytest.raises(SecretError) as e:
        parse_dotenv("DOCKER_HOST=tcp://x\n")
    assert e.value.code == "secret_name_reserved"


def test_parse_dotenv_never_echoes_an_invalid_key():
    with pytest.raises(SecretError) as e:
        parse_dotenv("abc+/SECRETPART=x\n")
    assert e.value.code == "dotenv_invalid"
    assert "SECRETPART" not in e.value.message


def test_parse_dotenv_reads_a_comment_after_an_empty_value_as_empty():
    text = "A= # set me\nB=#nothing\nC=\t# tab\nHASH=a#b\n"
    assert parse_dotenv(text) == {"A": "", "B": "", "C": "", "HASH": "a#b"}


def test_expected_never_asks_for_a_reserved_name():
    example = "DOCKER_HOST=\ncompose_file=\nAPI_KEY=\n"
    compose = "x: ${COMPOSE_PROJECT_NAME:?} ${Docker_Thing}\n"
    assert expected(example, compose) == {"API_KEY"}
