import pytest

from eggie_api.domain.secrets import (SecretError, check_hint, check_name,
                                    check_total, check_value, declared,
                                    is_reserved)


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


@pytest.mark.parametrize("name", ["COMPOSE_FILE", "docker_host", "LD_PRELOAD",
                                  "PATH", "home", "BUILDX_BUILDER", "buildkit_host"])
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


def test_declared_reads_mapping_and_list_environments():
    compose = {"services": {
        "web": {"environment": {"DATABASE_URL": "postgres://db/app", "X": None,
                                "Y": "${Y}", "PORT": 8080, "E": ""}},
        "worker": {"environment": ["A=1", "B", "C=${C:-d}", "D="]},
        "db": {"image": "postgres"},
    }}
    services, names = declared(compose)
    assert services == ["web", "worker", "db"]
    assert names == {"web": {"DATABASE_URL", "PORT"}, "worker": {"A"}, "db": set()}


def test_declared_tolerates_a_compose_without_services():
    assert declared({}) == ([], {})


@pytest.mark.parametrize("hint", ["", "x" * 501, "a\x00b", "bad \ud800", "a\nb", "\x1b[31m"])
def test_a_bad_hint_is_refused_with_its_code(hint):
    with pytest.raises(SecretError) as e:
        check_hint(hint)
    assert e.value.code == "secret_hint_invalid"


def test_a_hint_of_500_characters_with_a_url_is_accepted():
    check_hint("Stripe → Developers → API keys: https://dashboard.stripe.com/apikeys".ljust(500, "."))
