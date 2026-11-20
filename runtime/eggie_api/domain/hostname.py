from __future__ import annotations

import re
import unicodedata

from ..errors import Invalid
from .edge import is_hostname

_BAD = "That doesn't look like a domain name. Use something like example.com."
_XN = "Type this name in its xn-- form, as your registrar shows it."


def tidy(raw: str) -> str:
    """What an owner types or pastes, as the ASCII hostname the service keys on."""
    value = raw.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = re.split(r"[/?#]", value, maxsplit=1)[0].split(":", 1)[0]
    value = unicodedata.normalize("NFC", value.removesuffix(".").lower())
    if not value or any(c.isspace() for c in value):
        raise Invalid("invalid_domain", _BAD)
    try:
        encoded = value.encode("idna").decode("ascii")
        # The codec is IDNA2003: it maps `ß` to `ss`, a different domain.
        changed = any(not label.isascii() and label.encode("idna").decode("idna") != label
                      for label in value.split("."))
    except UnicodeError:
        raise Invalid("invalid_domain", _BAD) from None
    if changed:
        raise Invalid("invalid_domain", _XN)
    value = encoded
    if not is_hostname(value):
        raise Invalid("invalid_domain", _BAD)
    return value
