from __future__ import annotations

from fastapi import FastAPI

from ..config import ApiConfig
from ..wiring import Services, build
from . import auth, errors
from .routers import (account, files, github, jobs, lifecycle, projects, public,
                      secrets, sessions, system, uploads)

FEATURES = (system, account, github, projects, lifecycle, files, uploads,
            secrets, jobs, public)


def create_app(*, config: ApiConfig | None = None, services: Services | None = None,
               **overrides) -> FastAPI:
    """A factory on purpose: no module-level app, so importing this opens no
    sqlite file and starts no thread."""
    config = config or ApiConfig.from_env()
    services = services or build(config, **overrides)
    app = FastAPI(title="eggie-api", version=config.version)
    app.state.services = services
    errors.install(app)
    # Read once at startup, not per request. The API binds 0.0.0.0 inside
    # the VM (WSL2's localhostForwarding needs that), so every container in
    # the VM can otherwise reach an API holding the Docker socket. A missing
    # or empty token file must close every authenticated route, never open
    # one -- Phase 3 replaces this shared, VM-wide token with a service-issued
    # device token per host.
    auth.install(app, token=auth.read_token(config.token_path),
                 sessions=services.sessions, config=config)
    for feature in FEATURES:
        router = feature.build(services)
        app.include_router(router)
        app.include_router(router, prefix="/api")
    app.include_router(public.build_console(services), prefix="/api")
    app.include_router(sessions.build(services))
    return app
