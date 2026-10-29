"""The desktop app and the console it opens ship separately, so each declares
the dark palette itself; this keeps them the same palette."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "host" / "desktop" / "ui" / "app.css"
CONSOLE = ROOT / "runtime" / "web" / "packages" / "ui" / "src" / "tokens.css"
DARK = "@media (prefers-color-scheme: dark)"


def _hex_tokens(css: str) -> dict[str, str]:
    return {k: v.lower() for k, v in re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\b", css)}


def _dark(css: str) -> tuple[dict[str, str], dict[str, str]]:
    """(tokens the dark block declares, effective dark palette incl. inherited light :root)."""
    start = css.index(DARK)
    block = _hex_tokens(css[start:css.index("}", start)])
    return block, {**_hex_tokens(css[:start]), **block}


def test_desktop_dark_tokens_match_the_console():
    desktop, _ = _dark(DESKTOP.read_text(encoding="utf-8"))
    _, console = _dark(CONSOLE.read_text(encoding="utf-8"))
    shared = sorted(set(desktop) & set(console))
    assert len(shared) >= 20, shared
    assert {k: desktop[k] for k in shared} == {k: console[k] for k in shared}
