from __future__ import annotations

import re

from ..errors import Invalid
from .edge import is_hostname

_BAD = "That doesn't look like a domain name. Use something like example.com."


def tidy(raw: str) -> str:
    """What an owner types or pastes, as the ASCII hostname the service keys on."""
    value = raw.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = re.split(r"[/?#]", value, maxsplit=1)[0].split(":", 1)[0]
    value = value.removesuffix(".").lower()
    if not value or any(c.isspace() for c in value):
        raise Invalid("invalid_domain", _BAD)
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError:
        raise Invalid("invalid_domain", _BAD) from None
    if not is_hostname(value):
        raise Invalid("invalid_domain", _BAD)
    return value
