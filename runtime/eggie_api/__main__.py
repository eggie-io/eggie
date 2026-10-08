from __future__ import annotations

import uvicorn

from .config import ApiConfig
from .rest.app import create_app
from .infra.migrate import SchemaTooNew
from .wiring import build


def main() -> None:
    config = ApiConfig.from_env()
    try:
        services = build(config)
    except SchemaTooNew as e:
        # `restart: always` loops this container, so the log is all anyone has
        # to go on: one line that says what to do, not a traceback.
        raise SystemExit(f"eggie-api will not start: {e}") from None
    app = create_app(config=config, services=services)
    services.account.resume()
    services.sync.start()
    services.lifecycle.resume_all()
    # Defaults to 0.0.0.0 because the host reaches the API through the VM's
    # port mapping; narrowing the bind address is a later task's decision.
    uvicorn.run(app, host=config.bind_host, port=config.port)


if __name__ == "__main__":
    main()
