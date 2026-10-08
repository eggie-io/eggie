"""`eggie` inside the VM: turns a folder in ~/projects into a routed project.

Built into /usr/local/bin/eggie by install.sh, then run by whichever coding
agent the user works in. Stdlib only: it can import neither host/ nor
eggie_api/, so the names it shares with them are declared again here and
held equal by tests/test_constants_agree.py.
"""
from __future__ import annotations

from .api import START_STACK, RESTART_API, ApiClient, default_client, read_token
from .constants import (ALT_COMPOSE_FILES, API_PORT, API_UNCONFIGURED, COMPOSE_FILE,
                        DOCKER_GROUP, GUEST_PROJECTS, GUEST_ROOT, GUEST_STACK,
                        GUEST_TOKEN, VERIFY_PROJECT_ID)
from .env import Env
from .errors import ApiError, EggieError, JobFailed
from .main import main
from .project import (docker_gid, prepare_overlay_dir, project_id_for, project_of,
                      repo_name, require_id)
from .secrets import RESERVED_NAMES, RESERVED_PREFIXES

__all__ = [name for name in dir() if not name.startswith("_")]
