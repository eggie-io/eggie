"""Title bar colours for Windows 11: pure value mapping, no ctypes."""
import re
from pathlib import Path

from host.providers.title_bar_win import COLORS, apply, colorref

CSS = Path(__file__).resolve().parents[2] / "host" / "desktop" / "ui" / "app.css"


def test_colorref_is_bgr():
    assert colorref("#fefbf7") == 0x00F7FBFE
    assert colorref("#2A1F16") == 0x00161F2A


def test_light_title_bar_sets_caption_text_border_and_light_buttons():
    calls = []
    apply(7, False, lambda h, a, v: calls.append((h, a, v)))
    assert sorted(calls) == sorted([(7, 20, 0), (7, 34, 0x00D9E6EF),
                                    (7, 35, 0x00F7FBFE), (7, 36, 0x0048586A)])


def test_dark_title_bar_uses_dark_tokens_and_dark_buttons():
    calls = []
    apply(7, True, lambda h, a, v: calls.append((a, v)))
    assert dict(calls) == {20: 1, 34: colorref("#3a2f25"),
                           35: colorref("#211a14"), 36: colorref("#cdb9a4")}


def test_an_unsupported_attribute_does_not_stop_the_others():
    # Windows 10 rejects caption/text/border colours; dark mode must still apply.
    seen = []

    def setter(h, a, v):
        seen.append(a)
        if a in (34, 35, 36):
            raise OSError("E_INVALIDARG")

    apply(7, True, setter)
    assert sorted(seen) == [20, 34, 35, 36]


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def test_the_title_bar_colours_match_the_page_tokens():
    css = CSS.read_text(encoding="utf-8")
    dark_start = css.index("@media (prefers-color-scheme: dark)")
    light, dark = _tokens(css[:dark_start]), _tokens(css[dark_start:])
    assert "raised" in light and "raised" in dark
    for theme, tokens in (("light", light), ("dark", dark)):
        assert COLORS[theme] == {"caption": tokens["raised"], "text": tokens["ink-2"],
                                 "border": tokens["line"]}
