from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..errors import (BadRequest, Conflict, DiskFull, EggieError, Forbidden, Invalid,
                      NotFound, TooLarge, Unauthorized, Unavailable, Upstream)

log = logging.getLogger("eggie.api")

STATUS: dict[type[EggieError], int] = {
    NotFound: 404, Conflict: 409, Invalid: 422, BadRequest: 400,
    Unauthorized: 401, Forbidden: 403, TooLarge: 413, DiskFull: 507,
    Upstream: 502, Unavailable: 503,
}


def status_of(exc: EggieError) -> int:
    for cls in type(exc).__mro__:
        if cls in STATUS:
            return STATUS[cls]
    return 500


def error_body(code: str, message: str, status: int,
               extra: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **(extra or {})}},
                        status_code=status)


def _validation_message(exc: RequestValidationError) -> str:
    first = (exc.errors() or [{}])[0]
    where = ".".join(str(p) for p in first.get("loc", ())[1:])
    return f"{where}: {first.get('msg', 'invalid request body')}".lstrip(": ")


def install(app: FastAPI) -> None:
    @app.exception_handler(EggieError)
    async def _eggie_error(_request, exc: EggieError):
        return error_body(exc.code, exc.message, status_of(exc), exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(_request, exc: RequestValidationError):
        return error_body("invalid_request", _validation_message(exc), 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error")
        return error_body(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_request, exc: Exception):
        # The traceback goes to the API's log, never into the response: the
        # host client only needs a code it can act on.
        log.exception("unhandled error serving a request")
        return error_body("internal_error",
                          "Something went wrong inside the Eggie service in the "
                          "virtual machine. Try the same command again; if it keeps "
                          "failing, run setup again.\n"
                          f"(unexpected {type(exc).__name__}; the details are in the "
                          "service's own log)", 500)
