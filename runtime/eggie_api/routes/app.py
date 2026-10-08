from __future__ import annotations

import logging
import os
import secrets
import tempfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               Response, StreamingResponse)
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import constants
from ..config import ApiConfig
from ..errors import (BadRequest, Conflict, DiskFull, EggieError, Forbidden, Invalid,
                      NotFound, TooLarge, Unauthorized, Unavailable, Upstream)
from ..infra import disk
from ..infra.cloud import Cloud
from ..infra.github import GitHub
# Imported by name: the /health route below shadows a module named `health`.
from ..infra.health import default_probe
from ..infra.runner import LocalRunner
from ..infra.db import Database
from ..infra.repos import Repos
from ..infra.tunnel import TunnelClient
from ..infra.uploads import CHUNK_SIZE, UploadStore
from ..services.account import Account
from ..services.agents import AgentStatus
from ..services.clone import CloneService
from ..services.files import FileService
from ..services.files import disk_full as _disk_full
from ..services.github_link import GitHubLink
from ..services.jobs import JobRegistry
from ..services.lifecycle import LifecycleService
from ..services.loader import ProjectLoader
from ..services.locks import ProjectLocks
from ..services.projects import ProjectService
from ..services.public import Public
from ..services.secrets import SecretService
from ..services.sessions import COOKIE, HANDOFF_TTL, SESSION_TTL, Sessions
from ..services.sync import SyncLoop, run_pass
from ..services.system import SystemService
from ..services.uploads import UploadService

TEXT = "text/plain; charset=utf-8"
log = logging.getLogger("eggie.api")


STATUS: dict[type[EggieError], int] = {
    NotFound: 404, Conflict: 409, Invalid: 422, BadRequest: 400,
    Unauthorized: 401, Forbidden: 403, TooLarge: 413, DiskFull: 507,
    Upstream: 502, Unavailable: 503,
}


def _status_of(exc: EggieError) -> int:
    for cls in type(exc).__mro__:
        if cls in STATUS:
            return STATUS[cls]
    return 500


class WebOverride(BaseModel):
    service: str
    port: int
    subdomain: str | None = None


class CreateProject(BaseModel):
    id: str
    web: list[WebOverride] | None = None
    domain: str | None = None


class StartUpload(BaseModel):
    path: str
    size: int = Field(ge=0)
    fingerprint: str = ""
    replace: bool = False


class Handoff(BaseModel):
    code: str


class SecretValue(BaseModel):
    value: str


class SecretHint(BaseModel):
    hint: str


class CloneRepo(BaseModel):
    repo: str
    id: str | None = None


def _body(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}},
                        status_code=status)


def _validation_message(exc: RequestValidationError) -> str:
    first = (exc.errors() or [{}])[0]
    where = ".".join(str(p) for p in first.get("loc", ())[1:])
    return f"{where}: {first.get('msg', 'invalid request body')}".lstrip(": ")


def _size_words(byte_count: int) -> str:
    """Megabytes for anything a real project could reach; bytes below that, so
    a small cap never reads as "0 MB"."""
    megabytes = byte_count / (1024 * 1024)
    return f"{megabytes:.0f} MB" if megabytes >= 1 else f"{byte_count} bytes"


def _read_token(path: Path) -> str:
    """Empty string for "missing", "unreadable", and "unparseable" alike --
    callers only need to know whether they have a credential to compare
    against, and this must fail closed on anything unexpected rather than
    crash-loop the API."""
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def create_app(*, config: ApiConfig | None = None, runner=None, repos=None,
               jobs: JobRegistry | None = None, http_probe=None,
               sessions: Sessions | None = None, cloud=None,
               account: Account | None = None,
               public: Public | None = None, github=None,
               github_link: GitHubLink | None = None) -> FastAPI:
    config = config or ApiConfig.from_env()
    runner = runner or LocalRunner()
    http_probe = http_probe or default_probe
    repos = repos if repos is not None else Repos.open(Database(config.state_db))
    jobs = jobs or JobRegistry()
    loader = ProjectLoader(config, repos.projects)
    secrets_svc = SecretService(repos.secrets, loader)
    system = SystemService(config, runner)
    locks = ProjectLocks()
    sessions = sessions if sessions is not None else Sessions(repos.sessions)
    uploads = UploadStore(config.uploads_root,
                          free_bytes=lambda: disk.usage(
                              Path(config.projects_root))["free_bytes"])
    uploads.sweep()
    uploads_svc = UploadService(uploads, loader, locks)
    files_svc = FileService(loader, locks)
    cloud = cloud or Cloud(config.cloud_url)
    account = account or Account(repos.account, repos.cloud_projects, cloud)

    public = public or Public(
        projects=repos.projects, cloud_projects=repos.cloud_projects,
        account=account, cloud=cloud,
        client=TunnelClient(runner, config.stack_file),
        token_path=config.tunnel_token_path,
        origin=f"http://{config.traefik_host}:{config.edge_port}",
        hosts_for=loader.public_hosts)
    account.on_forget = public.forget_local
    account.before_sign_out = lambda: public.release_all()

    def sync_pass() -> None:
        try:
            public.reconcile()
        except Exception:
            log.exception("reconciling public URLs failed")
        run_pass(account, cloud, repos)

    sync = SyncLoop(sync_pass)
    account.on_signed_in = sync.wake
    github = github or GitHub(config.github_url, config.github_api_url)
    github_link = github_link or GitHubLink(
        repos.github, github, client_id=config.github_client_id,
        directory=config.github_dir)
    agent_status = AgentStatus(config.agent_status_dir, config.agents_dir)
    lifecycle_svc = LifecycleService(config, runner, loader, repos.projects,
                                     secrets_svc, jobs, locks, http_probe)
    clone_svc = CloneService(config, runner, loader, repos.projects, locks, jobs,
                             github_link, lifecycle_svc, sync.wake)
    projects_svc = ProjectService(config, runner, loader, repos.projects, secrets_svc,
                                  jobs, locks, uploads_svc, public, http_probe,
                                  sync.wake)

    app = FastAPI(title="eggie-api", version=config.version)
    app.state.config = config
    app.state.runner = runner
    app.state.sessions = sessions
    app.state.github = github_link
    app.state.services = SimpleNamespace(
        repos=repos, jobs=jobs, public=public, account=account, sync=sync,
        lifecycle=lifecycle_svc, projects=projects_svc)
    router = APIRouter()
    # Mounted only at /api: turning a public URL on or off is the console's
    # decision, never the guest token's (CLI, coding agents).
    console_router = APIRouter()

    @app.exception_handler(EggieError)
    async def _eggie_error(_request, exc: EggieError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message,
                                       **exc.extra}}, status_code=_status_of(exc))

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(_request, exc: RequestValidationError):
        return _body("invalid_request", _validation_message(exc), 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error")
        return _body(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_request, exc: Exception):
        # The traceback goes to the API's log, never into the response: the
        # host client only needs a code it can act on.
        log.exception("unhandled error serving a request")
        return _body("internal_error",
                     "Something went wrong inside the Eggie service in the "
                     "virtual machine. Try the same command again; if it keeps "
                     "failing, run setup again.\n"
                     f"(unexpected {type(exc).__name__}; the details are in the "
                     "service's own log)", 500)

    # Read once at startup, not per request. The API binds 0.0.0.0 inside
    # the VM (WSL2's localhostForwarding needs that), so every container in
    # the VM can otherwise reach an API holding the Docker socket. A missing
    # or empty token file must close every authenticated route, never open
    # one -- Phase 3 replaces this shared, VM-wide token with a service-issued
    # device token per host.
    token = _read_token(config.token_path)

    allowed_hosts = {f"localhost:{config.edge_port}",
                     f"127.0.0.1:{config.edge_port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    # Reachable before sign-in: checking the API version, and trading a
    # handoff code for a cookie. GET/DELETE /api/session must still go
    # through the session check below -- only the exchange itself is open.
    open_browser_routes = {("GET", "/api/health"), ("HEAD", "/api/health"),
                           ("POST", "/api/session")}

    def _bearer_ok(request: Request) -> bool:
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        # Starlette decodes headers as latin-1, so a header value can carry
        # bytes that are not valid ASCII; compare_digest raises TypeError on
        # two `str` args if either has a non-ASCII character. Comparing the
        # encoded bytes instead means every wire-valid header reaches a
        # normal true/false answer, never an exception out of the one guard
        # that must never throw.
        return (scheme.lower() == "bearer"
                and secrets.compare_digest(supplied.encode(), token.encode()))

    def _browser_refusal(request: Request) -> JSONResponse | None:
        if request.headers.get("host", "") not in allowed_hosts:
            return _body("forbidden_host",
                         "this address is not where Eggie's page lives", 403)
        if (request.method not in ("GET", "HEAD")
                and request.headers.get("origin", "") not in allowed_origins):
            return _body("forbidden_origin",
                         "requests that change something must come from "
                         "Eggie's own page", 403)
        return None

    @app.middleware("http")
    async def _authenticate(request: Request, call_next):
        path = request.url.path
        if path == "/api" or path.startswith("/api/"):
            refusal = _browser_refusal(request)
            if refusal is not None:
                return refusal
            if (request.method, path) in open_browser_routes:
                return await call_next(request)
            session_id = request.cookies.get(COOKIE)
            verdict = sessions.check(session_id)
            if verdict == "ok":
                response = await call_next(request)
                if verdict.extended:
                    response.set_cookie(COOKIE, session_id, max_age=SESSION_TTL,
                                        httponly=True, samesite="strict",
                                        path="/api")
                return response
            if verdict == "expired":
                return _body("session_expired", "your sign-in ran out; open "
                             "Eggie from the desktop app again", 401)
            return _body("not_signed_in", "open Eggie from the desktop app "
                         "to sign in", 401)
        if path == "/health":
            return await call_next(request)
        if not token:
            return _body(constants.API_UNCONFIGURED,
                         "the API has no token configured; run setup again", 503)
        if not _bearer_ok(request):
            return _body("unauthorized", "missing or invalid bearer token", 401)
        return await call_next(request)

    def require_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise NotFound("job_not_found", f"no job with id '{job_id}'")
        return job

    @router.get("/health")
    def health() -> dict:
        return system.health()

    @router.get("/version")
    def version() -> dict:
        return system.version()

    @router.get("/disk")
    def disk_usage() -> dict:
        return system.disk()

    @router.get("/connect")
    def connect_facts() -> dict:
        return system.connect()

    @router.get("/agents/status")
    def agents_status() -> dict:
        return agent_status.status()

    @router.post("/agents/{agent_id}/setup")
    def agent_setup(agent_id: str) -> dict:
        return agent_status.ensure_setup(agent_id)

    @router.get("/account")
    def account_status() -> dict:
        return account.status()

    @router.post("/account/sign-in")
    def account_sign_in() -> dict:
        return account.start_sign_in()

    @router.post("/account/sign-out")
    def account_sign_out() -> dict:
        return account.sign_out()

    @router.get("/github")
    def github_status() -> dict:
        github_link.check_token()
        return github_link.status()

    @router.post("/github/connect")
    def github_connect() -> dict:
        return github_link.connect()

    @router.post("/github/disconnect")
    def github_disconnect() -> dict:
        return github_link.disconnect()

    @router.post("/github/reapply")
    def github_reapply() -> dict:
        return github_link.reapply()

    @router.get("/github/repos")
    def github_repos(page: int = 1) -> dict:
        return github_link.repos(page)

    @router.post("/github/clone", status_code=202)
    def github_clone(body: CloneRepo) -> dict:
        return clone_svc.clone(body.repo, body.id)

    @router.post("/projects", status_code=201)
    def create_project(body: CreateProject) -> dict:
        return projects_svc.create(
            body.id, [w.model_dump() for w in body.web] if body.web else None,
            body.domain)

    @router.post("/projects/{project_id}/adopt", status_code=201)
    def adopt_project(project_id: str) -> dict:
        return projects_svc.adopt(project_id)

    @router.get("/projects")
    def list_projects() -> dict:
        return projects_svc.list()

    @router.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        return projects_svc.get(project_id)

    @router.get("/projects/{project_id}/public")
    def public_status(project_id: str) -> dict:
        loader.require(project_id)
        return public.status(project_id)

    @console_router.post("/projects/{project_id}/public", status_code=202)
    def public_on(project_id: str) -> dict:
        loader.require(project_id)
        return public.enable(project_id)

    @console_router.delete("/projects/{project_id}/public")
    def public_off(project_id: str) -> dict:
        loader.require(project_id)
        return public.disable(project_id)

    @router.post("/projects/{project_id}/uploads", status_code=201)
    def start_upload(project_id: str, body: StartUpload) -> dict:
        return uploads_svc.start(project_id, path=body.path, size=body.size,
                                 fingerprint=body.fingerprint, replace=body.replace)

    @router.get("/projects/{project_id}/uploads")
    def pending_uploads(project_id: str) -> dict:
        return uploads_svc.list(project_id)

    @router.get("/uploads/{upload_id}")
    def upload_status(upload_id: str) -> dict:
        return uploads_svc.get(upload_id)

    @router.patch("/uploads/{upload_id}")
    async def upload_chunk(upload_id: str, request: Request) -> dict:
        try:
            offset = int(request.headers.get("upload-offset", ""))
        except ValueError:
            raise BadRequest("invalid_request", "Upload-Offset must be a number") from None
        body = bytearray()
        async for piece in request.stream():
            body += piece
            if len(body) > 2 * CHUNK_SIZE:
                raise TooLarge("payload_too_large", "send chunks of at most "
                               f"{CHUNK_SIZE} bytes")
        # Both append() and finish() do blocking file I/O; run them off the
        # event loop so one slow upload can't stall every other request.
        return await run_in_threadpool(uploads_svc.append, upload_id, offset,
                                       bytes(body))

    @router.delete("/uploads/{upload_id}")
    def cancel_upload(upload_id: str) -> dict:
        return uploads_svc.cancel(upload_id)

    async def _stream_to_tempfile(request: Request, dir_: Path) -> Path:
        # Written next to its destination, never buffered whole in memory -
        # this route body is what removes the old ~24 KB command-line ceiling.
        dir_.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=dir_, suffix=".upload")
        path = Path(name)
        written = 0
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in request.stream():
                    written += len(chunk)
                    # Checked before writing: the moment the limit is passed,
                    # not after the whole body has already landed on disk.
                    if written > config.max_upload_bytes:
                        raise TooLarge(
                            "payload_too_large",
                            "This project is larger than the "
                            f"{_size_words(config.max_upload_bytes)} an upload "
                            "may be. Remove the large files or folders from it "
                            "-- build output, videos and database files are the "
                            "usual cause -- and try again.")
                    try:
                        f.write(chunk)
                    except OSError as e:
                        if disk.is_disk_full(e):
                            raise _disk_full() from e
                        raise
        except BaseException:
            # A client that disconnects mid-upload, or trips the size cap
            # above, must not leave a temp file behind.
            path.unlink(missing_ok=True)
            raise
        return path

    @router.post("/projects/{project_id}/files")
    async def upload_files(project_id: str, request: Request) -> dict:
        return await files_svc.extract(
            project_id, lambda dir_: _stream_to_tempfile(request, dir_))

    @router.get("/projects/{project_id}/files")
    def list_files(project_id: str, dir: str | None = None) -> dict:
        return files_svc.list(project_id, dir)

    @router.put("/projects/{project_id}/files/{file_path:path}")
    async def write_file(project_id: str, file_path: str, request: Request) -> dict:
        return await files_svc.write(
            project_id, file_path, lambda dir_: _stream_to_tempfile(request, dir_))

    @router.get("/projects/{project_id}/files/{file_path:path}")
    def read_file(project_id: str, file_path: str):
        target = files_svc.file(project_id, file_path)
        # Starlette streams this from disk; the file is never read whole.
        return FileResponse(target, filename=target.name)

    @router.delete("/projects/{project_id}/files/{file_path:path}")
    def delete_file(project_id: str, file_path: str) -> dict:
        return files_svc.delete(project_id, file_path)

    @router.get("/projects/{project_id}/secrets")
    def list_secrets(project_id: str) -> dict:
        return secrets_svc.list(project_id)

    @router.put("/projects/{project_id}/secrets/{name}", status_code=204)
    def put_secret(project_id: str, name: str, body: SecretValue) -> Response:
        secrets_svc.put(project_id, name, body.value)
        return Response(status_code=204)

    @router.put("/projects/{project_id}/secret-requests/{name}", status_code=204)
    def request_secret(project_id: str, name: str, body: SecretHint) -> Response:
        secrets_svc.request(project_id, name, body.hint)
        return Response(status_code=204)

    @router.delete("/projects/{project_id}/secrets/{name}", status_code=204)
    def delete_secret(project_id: str, name: str) -> Response:
        secrets_svc.delete(project_id, name)
        return Response(status_code=204)

    @router.get("/projects/{project_id}/delete-preview")
    def delete_preview(project_id: str) -> dict:
        return projects_svc.delete_preview(project_id)

    @router.delete("/projects/{project_id}")
    def delete_project(project_id: str, purge: bool = False) -> dict:
        return projects_svc.delete(project_id, purge=purge)

    @router.post("/projects/{project_id}/up", status_code=202)
    def project_up(project_id: str) -> dict:
        return {"job_id": lifecycle_svc.start(project_id)}

    @router.post("/projects/{project_id}/restart", status_code=202)
    def project_restart(project_id: str) -> dict:
        return {"job_id": lifecycle_svc.start(project_id, stop_first=True)}

    @router.post("/projects/{project_id}/down", status_code=202)
    def project_down(project_id: str) -> dict:
        return {"job_id": lifecycle_svc.stop(project_id)}

    @router.get("/projects/{project_id}/logs")
    def project_logs(project_id: str, follow: bool = False,
                     service: str | None = None):
        if not follow:
            return PlainTextResponse(lifecycle_svc.logs(project_id, service),
                                     media_type=TEXT)
        return StreamingResponse(lifecycle_svc.logs_stream(project_id, service),
                                 media_type=TEXT)

    @router.get("/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        return require_job(job_id).as_dict()

    @router.get("/jobs/{job_id}/logs")
    def job_logs(job_id: str, follow: bool = False):
        job = require_job(job_id)
        if not follow:
            return PlainTextResponse(job.text(), media_type=TEXT)
        return StreamingResponse(jobs.follow(job_id), media_type=TEXT)

    @app.post("/sessions/handoff")
    def issue_handoff() -> dict:
        return {"code": sessions.issue_handoff(), "expires_in": HANDOFF_TTL}

    # A signed-in page signing the system browser in (the desktop window's
    # "open in browser"). The middleware has already checked cookie and Origin.
    @app.post("/api/sessions/handoff")
    def issue_browser_handoff() -> dict:
        return issue_handoff()

    @app.post("/api/session")
    def start_session(body: Handoff, response: Response) -> dict:
        session_id = sessions.redeem(body.code)
        if session_id is None:
            raise Unauthorized("handoff_invalid", "that sign-in link has already "
                           "been used or has run out; open Eggie from the "
                           "desktop app again")
        response.set_cookie(COOKIE, session_id, max_age=SESSION_TTL,
                            httponly=True, samesite="strict", path="/api")
        return {"signed_in": True}

    @app.get("/api/session")
    def read_session() -> dict:
        # Reaching here means the middleware's own session check already
        # returned "ok" -- GET is gated like any other /api/* route.
        return {"signed_in": True}

    @app.delete("/api/session")
    def end_session(request: Request, response: Response) -> dict:
        sessions.end(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/api")
        return {"signed_in": False}

    app.include_router(router)
    app.include_router(router, prefix="/api")
    app.include_router(console_router, prefix="/api")

    return app
