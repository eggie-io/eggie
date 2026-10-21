from __future__ import annotations

import json
from pathlib import Path

from .. import constants

_KINDS = {"wsl", "lima", "site"}


def facts(path: Path, site_host: str | None = None,
          site_user: str | None = None) -> dict:
    """How a coding agent reaches the VM, from what
    install.sh recorded. Anything unreadable reads as an unknown VM: the
    console then chooses a guide from the browser instead."""
    try:
        recorded = json.loads(path.read_text())
    except (OSError, ValueError):
        recorded = {}
    if not isinstance(recorded, dict):
        recorded = {}
    vm = recorded.get("vm") if recorded.get("vm") in _KINDS else "other"
    user = recorded.get("user") if isinstance(recorded.get("user"), str) else ""
    ssh = None
    if vm == "lima" and user:
        ssh = {"host": "127.0.0.1", "port": constants.LIMA_SSH_PORT,
               "user": user, "key_file": constants.LIMA_KEY_FILE}
    elif vm == "site" and (site_user or user) and site_host:
        # The service installed the user's own key; there is no file to name.
        ssh = {"host": site_host, "port": 22, "user": site_user or user, "key_file": None}
    return {"vm": vm, "ssh": ssh}
