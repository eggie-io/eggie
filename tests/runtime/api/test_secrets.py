import pytest

from eggie_api.core.secrets import (SecretError, check_name, check_total,
                                    check_value, compose_defaulted, declared, defaults, importable,
                                    is_reserved, missing, parse_dotenv,
                                    parse_example)


@pytest.mark.parametrize("name", ["API_KEY", "_x", "a1"])
def test_ordinary_env_names_are_accepted(name):
    check_name(name)


@pytest.mark.parametrize("name", ["", "1KEY", "MY-KEY", "A B", "KÉY"])
def test_names_compose_cannot_pass_as_env_are_refused(name):
    with pytest.raises(SecretError) as e:
        check_name(name)
    assert e.value.code == "secret_name_invalid"


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


def test_missing_counts_compose_refs_without_a_default():
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
    assert missing({}, compose, have=set(), declared_names=set()) == ["A", "D", "E", "H"]


def test_compose_defaulted_lists_only_refs_that_carry_their_own_default():
    compose = ("environment:\n"
               "  - ${A} ${B:-x} ${C-y} $$D ${E:?e} ${F:+z} $${G:-g}\n"
               "# ${H:-h}\n")
    assert compose_defaulted(compose) == {"B", "C", "F"}


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


def test_parse_dotenv_never_echoes_an_invalid_key():
    with pytest.raises(SecretError) as e:
        parse_dotenv("abc+/SECRETPART=x\n")
    assert e.value.code == "dotenv_invalid"
    assert "SECRETPART" not in e.value.message


def test_parse_dotenv_reads_a_comment_after_an_empty_value_as_empty():
    text = "A= # set me\nB=#nothing\nC=\t# tab\nHASH=a#b\n"
    assert parse_dotenv(text) == {"A": "", "B": "", "C": "", "HASH": "a#b"}


@pytest.mark.parametrize("name", ["COMPOSE_FILE", "docker_host", "LD_PRELOAD",
                                  "PATH", "home"])
def test_names_the_runtime_reads_itself_are_reserved(name):
    assert is_reserved(name)
    with pytest.raises(SecretError) as e:
        check_name(name)
    assert e.value.code == "secret_name_reserved"


@pytest.mark.parametrize("name", ["PATHS", "MY_HOME", "LDAP_URL", "API_KEY"])
def test_lookalikes_are_not_reserved(name):
    assert not is_reserved(name)


def test_an_unpaired_surrogate_is_an_invalid_value_not_a_crash():
    with pytest.raises(SecretError) as e:
        check_value("a\ud800b")
    assert e.value.code == "secret_invalid_value"


def test_parse_dotenv_ignores_a_bom():
    assert parse_dotenv("\ufeffAPI_KEY=k\n") == {"API_KEY": "k"}


def test_parse_dotenv_returns_reserved_names_for_the_caller_to_handle():
    assert parse_dotenv("COMPOSE_PROJECT_NAME=x\nA=1\n") == {
        "COMPOSE_PROJECT_NAME": "x", "A": "1"}


def test_example_parsing_skips_broken_lines():
    text = ("APP_NAME=Laravel\nnot a pair\n1BAD=x\nCOMPOSE_FILE=x\n"
            "APP_KEY=\nQUOTED=\"a b\"\nLAST='never closed\nIGNORED=1\n")
    assert parse_example(text) == {"APP_NAME": "Laravel", "APP_KEY": "",
                                   "QUOTED": "a b"}


def test_example_of_none_is_empty():
    assert parse_example(None) == {}


def test_declared_reads_mapping_and_list_environments():
    compose = {"services": {
        "web": {"environment": {"DATABASE_URL": "postgres://db/app", "X": None,
                                "Y": "${Y}", "PORT": 8080}},
        "worker": {"environment": ["A=1", "B", "C=${C:-d}"]},
        "db": {"image": "postgres"},
    }}
    services, names = declared(compose)
    assert services == ["web", "worker", "db"]
    assert names == {"web": {"DATABASE_URL", "PORT"}, "worker": {"A"}, "db": set()}


def test_declared_tolerates_a_compose_without_services():
    assert declared({}) == ([], {})


def test_defaults_are_non_empty_and_never_reserved():
    assert defaults({"A": "1", "B": "", "PATH": "/x"}) == {"A": "1"}


def test_missing_combines_empty_example_keys_and_compose_refs():
    example = {"APP_KEY": "", "APP_NAME": "Laravel", "STRIPE_KEY": "",
               "DB_PASSWORD": "", "HOME": ""}
    compose = "services:\n  web:\n    environment:\n      A: ${OPENAI_KEY}\n      B: ${APP_NAME}\n      C: ${OPT:-x}\n"
    assert missing(example, compose, have={"STRIPE_KEY"},
                   declared_names={"DB_PASSWORD"}) == ["APP_KEY", "OPENAI_KEY"]


def test_importable_drops_empty_reserved_and_unchanged_values():
    values = {"APP_NAME": "Laravel", "APP_KEY": "base64:abc", "EMPTY": "",
              "COMPOSE_PROJECT_NAME": "x", "APP_ENV": "production"}
    assert importable(values, {"APP_NAME": "Laravel", "APP_ENV": "local"}) == {
        "APP_KEY": "base64:abc", "APP_ENV": "production"}
