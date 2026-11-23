import pytest

from eggie_api.domain.hostname import tidy
from eggie_api.errors import Invalid


@pytest.mark.parametrize("raw,want", [
    ("https://Example.com/", "example.com"),
    ("  WWW.Example.com:443/path?x#y ", "www.example.com"),
    ("example.com.", "example.com"),
    ("münchen.de", "xn--mnchen-3ya.de"),
])
def test_tidy(raw, want):
    assert tidy(raw) == want


@pytest.mark.parametrize("raw", ["", "   ", "localhost", "exa mple.com", "http://",
                                 "exa`mple.com", "a..example.com"])
def test_tidy_refuses(raw):
    with pytest.raises(Invalid) as e:
        tidy(raw)
    assert e.value.code == "invalid_domain"


def test_a_name_idna_would_turn_into_another_name_asks_for_its_xn_form():
    with pytest.raises(Invalid) as e:
        tidy("straße.de")
    assert (e.value.code, e.value.message) == (
        "invalid_domain", "Type this name in its xn-- form, as your registrar shows it.")


def test_a_name_typed_in_its_xn_form_is_kept():
    assert tidy("xn--mnchen-3ya.de") == "xn--mnchen-3ya.de"
