from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import constants
from .health import READY_TIMEOUT
from .. import __version__


@dataclass(frozen=True)
class ApiConfig:
    """Everything the API is allowed to vary at run time. Nothing else may
    hardcode the domain or the entry port.

    `projects_root` is the single source of truth for where projects live:
    uploads land there and `core/lifecycle.py` builds every compose `-f` path
    from the directory the API hands it, never from a constant of its own.
    In production it must stay under `/opt/omelet`, which is bind-mounted into
    the API container at the identical path -- compose files are parsed here
    but the bind-mount paths inside them are resolved by dockerd on the VM.
    """

    bind_host: str = "0.0.0.0"
    port: int = constants.API_PORT
    domain: str = constants.DEFAULT_DOMAIN
    edge_port: int = constants.EDGE_PORT
    # The name Traefik answers to on the `edge` network. Phase 5 moves the
    # proxy off this VM, where it stops being a sibling container.
    traefik_host: str = "traefik"
    # How long a project may take to answer through Traefik before the API
    # goes looking for a reason.
    ready_timeout: float = READY_TIMEOUT
    projects_root: Path = Path(constants.GUEST_PROJECTS)
    state_db: Path = Path(f"{constants.GUEST_ROOT}/state.db")
    # The shared secret the runtime installer generates in the guest. Phase 3
    # replaces it with a service-issued device token; see
    # omelet_api/routes/app.py's auth check.
    token_path: Path = Path(constants.GUEST_TOKEN)
    connect_path: Path = Path(constants.CONNECT_FILE)
    # Partial uploads, outside projects_root so neither a listing, the
    # reconcile scan nor the coding agent ever sees a half-written file.
    uploads_root: Path = Path(f"{constants.GUEST_ROOT}/uploads")
    # A runaway/abuse guard on file uploads, not a policy -- generous enough
    # that no real project hits it. Raise via env, no rebuild needed.
    max_upload_bytes: int = 512 * 1024 * 1024
    cloud_url: str = "https://omelet.bridgie.chat/api"
    stack_file: Path = Path(f"{constants.GUEST_ROOT}/stack.yml")
    tunnel_token_path: Path = Path(f"{constants.GUEST_ROOT}/tunnel/token")
    github_client_id: str = constants.GITHUB_CLIENT_ID
    github_url: str = "https://github.com"
    github_api_url: str = "https://api.github.com"
    github_dir: Path = Path(constants.GITHUB_DIR)
    version: str = __version__

    @classmethod
    def from_env(cls, env: dict | None = None) -> "ApiConfig":
        env = os.environ if env is None else env
        return cls(
            bind_host=env.get("OMELET_API_HOST", "0.0.0.0"),
            port=int(env.get("OMELET_API_PORT", constants.API_PORT)),
            domain=env.get("OMELET_DOMAIN", constants.DEFAULT_DOMAIN),
            edge_port=int(env.get("OMELET_EDGE_PORT", constants.EDGE_PORT)),
            traefik_host=env.get("OMELET_TRAEFIK_HOST", "traefik"),
            ready_timeout=float(env.get("OMELET_READY_TIMEOUT", READY_TIMEOUT)),
            projects_root=Path(env.get("OMELET_PROJECTS_ROOT",
                                       constants.GUEST_PROJECTS)),
            state_db=Path(env.get("OMELET_STATE_DB",
                                  f"{constants.GUEST_ROOT}/state.db")),
            token_path=Path(env.get("OMELET_API_TOKEN", constants.GUEST_TOKEN)),
            uploads_root=Path(env.get("OMELET_UPLOADS_ROOT",
                                      f"{constants.GUEST_ROOT}/uploads")),
            max_upload_bytes=int(env.get("OMELET_MAX_UPLOAD_BYTES",
                                         512 * 1024 * 1024)),
            cloud_url=env.get("OMELET_CLOUD_URL", "https://omelet.bridgie.chat/api"),
            stack_file=Path(env.get("OMELET_STACK_FILE",
                                    f"{constants.GUEST_ROOT}/stack.yml")),
            tunnel_token_path=Path(env.get("OMELET_TUNNEL_TOKEN",
                                           f"{constants.GUEST_ROOT}/tunnel/token")),
            github_client_id=env.get("OMELET_GITHUB_CLIENT_ID",
                                     constants.GITHUB_CLIENT_ID),
            github_url=env.get("OMELET_GITHUB_URL", "https://github.com"),
            github_api_url=env.get("OMELET_GITHUB_API_URL",
                                   "https://api.github.com"),
            github_dir=Path(env.get("OMELET_GITHUB_DIR", constants.GITHUB_DIR)),
            version=env.get("OMELET_SERVICE_VERSION", __version__),
        )
