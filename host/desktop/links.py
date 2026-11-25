"""`eggie://` links: a kitchen's console in the browser opens the app on it.

Any web page can open one, so a link only names a kitchen; the app shows its
agent picker if the signed-in account owns it, and Home otherwise.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

SCHEME = "eggie"
_KITCHEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def kitchen_from_link(url: str) -> str | None:
    """The kitchen id in `eggie://connect/<id>`; None for anything else."""
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return None
    if parts.scheme.lower() != SCHEME or parts.netloc.lower() != "connect":
        return None
    kitchen = unquote(parts.path.strip("/"))
    return kitchen if _KITCHEN_RE.fullmatch(kitchen) else None
