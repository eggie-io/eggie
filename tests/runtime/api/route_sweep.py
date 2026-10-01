"""Every route an app serves, for the auth sweeps."""
from __future__ import annotations


def every_route(app) -> list:
    # FastAPI 0.142 keeps an included router as one nested entry in
    # app.routes instead of copying its routes in; iter_route_contexts
    # flattens it. Older versions have no such function and a flat list.
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:
        return list(app.routes)
    return list(iter_route_contexts(app.routes))
