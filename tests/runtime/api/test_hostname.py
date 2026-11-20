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
