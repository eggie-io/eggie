from __future__ import annotations

import logging
import os
import secrets
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import yaml
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               Response, StreamingResponse)
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import constants
from ..config import ApiConfig
from ..domain.project import STARTED_OK, _slug
from ..errors import (BadRequest, Conflict, DiskFull, EggieError, Forbidden, Invalid,
                      NotFound, TooLarge, Unauthorized, Unavailable, Upstream)
from ..infra import disk, files
from ..infra import docker as lifecycle
from ..infra.cloud import Cloud
from ..infra.github import GitHub, auth_failed, clone_argv, redact, valid_repo
# Imported by name: the /health route below shadows a module named `health`.
from ..infra.health import answers, default_probe, diagnose
from ..infra.runner import LocalRunner
from ..infra.db import Database
from ..infra.repos import Repos
from ..infra.tunnel import TunnelClient
from ..infra.uploads import CHUNK_SIZE, UploadStore
from ..services.account import Account
from ..services.agents import AgentStatus
from ..services.files import FileService
from ..services.files import disk_full as _disk_full
from ..services.github_link import GitHubLink
from ..services.jobs import JobFailed, JobRegistry
from ..services.loader import ProjectLoader
from ..services.locks import ProjectLocks
from ..services.locks import busy as _busy
from ..services.public import Public
from ..infra.reconcile import discover, examine
from ..services.secrets import RUNNING, SecretService
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

    app = FastAPI(title="eggie-api", version=config.version)
    app.state.config = config
    app.state.runner = runner
    app.state.repos = repos
    app.state.jobs = jobs
    app.state.sessions = sessions
    app.state.account = account
    app.state.github = github_link
    app.state.sync = sync
    app.state.public = public
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

    def payload(row: dict, *, recheck: bool = False) -> dict:
        # A project with broken or missing files still has a status, and one
        # broken project must never take the whole listing down with it.
        problem = None
        urls: list[str] = []
        web: list[dict] = []
        project = None
        folder = loader.dir(row["id"])
        if not folder.is_dir():
            problem = {"code": "folder_missing",
                       "message": "this project's folder is gone"}
        else:
            try:
                project = loader.load(row["id"])
                urls = loader.urls_for(project, row["domain"])
                web = [{"url": url, "service": spec.service, "primary": index == 0}
                       for index, (url, spec) in enumerate(zip(urls, project.webs))]
            except EggieError as e:
                problem = {"code": e.code, "message": e.message}
        if problem is None and row.get("problem_code"):
            # A file that will not parse outranks a routing fault: it is why
            # the project has no URLs to be unreachable on.
            problem = {"code": row["problem_code"],
                       "message": row["problem_message"]}
            # One request with a short timeout, never the readiness window:
            # this runs inside a read the CLI is waiting on.
            if recheck and project is not None and answers(
                    project, row["domain"], edge_port=config.edge_port,
                    traefik_host=config.traefik_host, http_probe=http_probe):
                # An entrypoint slower than the readiness window stores a
                # diagnosis that is true for a minute and false forever after.
                repos.projects.set_problem(row["id"])
                problem = None
        active = jobs.active_for(row["id"])
        try:
            public_status = public.status(row["id"])
        except Exception:
            # A broken public-URL row must not take the whole listing down
            # with it; the rest of the project's status is still good.
            log.exception("reading the public URL status of %s failed", row["id"])
            public_status = {"state": "off", "note": None}
        return {"id": row["id"], "status": row["status"], "domain": row["domain"],
                "path": row["guest_path"], "urls": urls, "problem": problem,
                "empty": folder.is_dir() and not (folder / constants.COMPOSE_FILE).exists(),
                "web": web,
                "public": public_status,
                "first_run": row.get("last_started_at") is None,
                "restart_needed": secrets_svc.restart_needed(row),
                "secrets_requested": secrets_svc.requested_count(row["id"]),
                "job": None if active is None else {
                    "id": active.id, "kind": active.kind,
                    "phase": active.phase, "started_at": active.started_at}}

    def submit_locked(project_id: str, work, kind: str | None = None) -> str:
        """The job releases the lock itself, in its own `finally`."""
        locks.acquire_or_raise(project_id)
        try:
            return jobs.submit(work, kind=kind, project_id=project_id)
        except BaseException:
            locks.release(project_id)
            raise

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
        if not valid_repo(body.repo):
            raise Invalid("invalid_repo",
                          f"'{body.repo}' is not an owner/name repository")
        project_id = _slug(body.id or body.repo.split("/")[1])
        if not project_id:
            raise Invalid("invalid_project",
                          f"'{body.repo}' has no usable project name")
        directory = loader.dir(project_id)
        if repos.projects.get(project_id) is not None or directory.exists():
            raise Conflict("project_exists",
                           f"project '{project_id}' already exists")
        token = github_link.require_token()
        if not locks.acquire(project_id):
            raise _busy(project_id)

        def work(write):
            handed_over = False
            # Cloned next to the real folder, not into it: nothing here holds
            # `directory`'s name reserved while git runs, so another request
            # (POST /projects, an adopt, a coding agent's own mkdir) can claim
            # it first. Cloning into a dot-prefixed staging dir -- which
            # `reconcile.discover` already skips -- and renaming in only once
            # the name is still free means a failure can never touch a folder
            # this job did not create.
            staging = Path(config.projects_root) / f".clone-{uuid.uuid4().hex[:12]}"
            try:
                write.phase("cloning")
                write(f"git clone https://github.com/{body.repo}.git\n")
                result = runner.exec(clone_argv(body.repo, staging),
                                     env={"EGGIE_GH_TOKEN": token,
                                          "GIT_TERMINAL_PROMPT": "0"})
                if not result.ok:
                    output = redact((result.stderr or result.stdout).strip(), token)
                    shutil.rmtree(staging, ignore_errors=True)
                    if auth_failed(output):
                        github_link.confirm_bad(token)
                    raise JobFailed(output or "git clone failed")
                if directory.exists() or repos.projects.get(project_id) is not None:
                    shutil.rmtree(staging, ignore_errors=True)
                    raise JobFailed(f"a project called '{project_id}' appeared "
                                    "while downloading; nothing was changed")
                os.rename(staging, directory)
                repos.projects.add(project_id, str(directory), config.domain)
                sync.wake()
                if not (directory / constants.COMPOSE_FILE).exists():
                    write(f"no {constants.COMPOSE_FILE} yet; left stopped\n")
                    return {"id": project_id, "status": "stopped"}
                try:
                    up = start_work(project_id, stop_first=False)
                except EggieError as e:
                    write(f"{e.message}\n")
                    return {"id": project_id, "status": "stopped"}
                # start_work's job releases the lock in its own finally.
                handed_over = True
                return {"id": project_id, **up(write)}
            finally:
                if not handed_over:
                    locks.release(project_id)

        try:
            job_id = jobs.submit(work, kind="clone", project_id=project_id)
        except BaseException:
            locks.release(project_id)
            raise
        return {"job_id": job_id, "id": project_id}

    @router.post("/projects", status_code=201)
    def create_project(body: CreateProject) -> dict:
        # Same slug rule load_project applies to a directory name, so an id
        # survives the round trip host -> API -> compose project name.
        project_id = _slug(body.id)
        if not project_id:
            raise Invalid("invalid_project",
                          f"'{body.id}' is not a usable project id")
        if repos.projects.get(project_id) is not None:
            raise Conflict("project_exists",
                           f"project '{project_id}' already exists")

        d = loader.dir(project_id)
        d.mkdir(parents=True, exist_ok=True)
        if body.web:
            (d / ".eggie").mkdir(exist_ok=True)
            (d / ".eggie" / "project.yml").write_text(yaml.safe_dump(
                {"id": project_id, "web": [w.model_dump() for w in body.web]},
                sort_keys=False))
        repos.projects.add(project_id, str(d), body.domain or config.domain)
        sync.wake()
        return payload(repos.projects.get(project_id))

    @router.post("/projects/{project_id}/adopt", status_code=201)
    def adopt_project(project_id: str) -> dict:
        if repos.projects.get(project_id) is not None:
            raise Conflict("project_exists",
                           f"project '{project_id}' already exists")
        folder = loader.dir(project_id)
        if not folder.is_dir():
            raise NotFound("folder_not_found",
                           f"no folder '{project_id}' in the projects folder")
        found = examine(folder)
        if not found.adoptable:
            raise Conflict("not_adoptable",
                           f"'{project_id}' cannot be adopted: {found.reason}")
        repos.projects.add(project_id, str(folder), config.domain)
        sync.wake()
        return payload(repos.projects.get(project_id))

    @router.get("/projects")
    def list_projects() -> dict:
        # `eggie status` is the surface users actually read, so a stale
        # diagnosis has to clear here too. payload() only probes a row that
        # carries a stored problem -- normally none -- so an ordinary listing
        # still pays no round trips at all.
        rows = repos.projects.list()
        known = {row["id"] for row in rows}
        return {"projects": [payload(row, recheck=True) for row in rows],
                "discovered": [asdict(d) for d in
                               discover(Path(config.projects_root), known)]}

    @router.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        return payload(loader.require(project_id), recheck=True)

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

    def compose_name_for(row: dict) -> str:
        return row.get("compose_name") or row["id"]

    def resolve_compose_name(project_id: str, row: dict) -> str:
        return lifecycle.resolve_compose_name(
            runner, loader.dir(project_id), compose_name_for(row))

    @router.get("/projects/{project_id}/delete-preview")
    def delete_preview(project_id: str) -> dict:
        row = loader.require(project_id)
        return {**files.tree_stats(loader.dir(project_id)),
                **lifecycle.project_resources(
                    runner, resolve_compose_name(project_id, row))}

    @router.delete("/projects/{project_id}")
    def delete_project(project_id: str, purge: bool = False) -> dict:
        row = loader.require(project_id)
        folder = loader.dir(project_id)
        # Synchronous: the host CLI, install verification and the desktop's
        # replace-import all wait on this answer. Removal is by compose label,
        # not `compose down`, so a broken compose file can never block it.
        with locks.held(project_id):
            public.disable(project_id, force=True)
            result = lifecycle.remove_by_label(
                runner, resolve_compose_name(project_id, row), volumes=purge)
            if purge:
                uploads_svc.drop_project(project_id)
                secrets_svc.drop(project_id)
            if purge and folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
                if folder.exists():
                    removed = lifecycle.remove_tree_as_root(runner, folder)
                    if not removed.ok and result.ok:
                        result = removed
            repos.projects.remove(project_id)
            sync.wake()
        return {"id": project_id, "stopped": result.ok,
                "detail": "" if result.ok else (result.stderr or result.stdout).strip()}

    def start_work(project_id: str, *, stop_first: bool):
        row = loader.require(project_id)
        # Parsing happens here, not in the job, so a broken compose file comes
        # back as an error code the caller can read instead of a failed job.
        project = loader.load(project_id)
        compose = loader.parse_yaml(loader.dir(project_id) / constants.COMPOSE_FILE)
        services, declared_by = secrets_svc.declared(compose)
        name = str(compose.get("name") or project_id)
        domain = row["domain"]
        directory = loader.dir(project_id)

        def work(write):
            try:
                if stop_first:
                    write(f"compose down {project_id}\n")
                    down_result = lifecycle.compose_down(
                        runner, directory, env=secrets_svc.values(project_id))
                    if not down_result.ok:
                        raise JobFailed(
                            (down_result.stderr or down_result.stdout).strip()
                            or "compose down failed")
                # Recorded before compose runs, not after a successful start:
                # delete must be able to find these containers by name even
                # when `up` never reaches STARTED_OK.
                repos.projects.set_compose_name(project_id, name)
                write(f"compose up {project_id}\n")
                # Stamped before the values are read, so a secret changed while
                # compose runs still shows as needing a restart.
                began = time.time()
                values = secrets_svc.values(project_id) or {}
                status, detail = lifecycle.compose_up(
                    runner, project, directory, domain, on_phase=write.phase,
                    services=services, secrets=values, declared=declared_by)
                diagnosis = None
                if status in RUNNING:
                    # A crash-looping stack still got these values.
                    repos.projects.mark_started(project_id, began)
                if status == STARTED_OK:
                    write.phase("checking")
                    write("waiting for the project to answer through Traefik\n")
                    diagnosis = diagnose(
                        runner, project, domain, directory=directory,
                        env=values or None,
                        edge_port=config.edge_port,
                        traefik_host=config.traefik_host, http_probe=http_probe,
                        timeout=config.ready_timeout)
                repos.projects.set_status(project_id, status)
                if diagnosis is None:
                    repos.projects.set_problem(project_id)
                else:
                    repos.projects.set_problem(project_id, diagnosis.code,
                                      diagnosis.message)
                result = {"status": status, "urls": loader.urls_for(project, domain),
                          "problem": diagnosis.as_dict() if diagnosis else None}
                write(f"status: {status}\n")
                if diagnosis:
                    # The containers did start, so the job succeeds; the reason
                    # the URL will not answer belongs in its log all the same.
                    write(f"{diagnosis.message}\n")
                if detail:
                    write(f"{detail}\n")
                if status != STARTED_OK:
                    raise JobFailed(
                        detail or f"containers did not stay up (status: {status})",
                        result=result)
                return result
            finally:
                locks.release(project_id)

        return work

    @router.post("/projects/{project_id}/up", status_code=202)
    def project_up(project_id: str) -> dict:
        return {"job_id": submit_locked(
            project_id, start_work(project_id, stop_first=False), "up")}

    @router.post("/projects/{project_id}/restart", status_code=202)
    def project_restart(project_id: str) -> dict:
        return {"job_id": submit_locked(
            project_id, start_work(project_id, stop_first=True), "restart")}

    @router.post("/projects/{project_id}/down", status_code=202)
    def project_down(project_id: str) -> dict:
        loader.require(project_id)

        def work(write):
            try:
                write.phase("stopping")
                write(f"compose down {project_id}\n")
                result = lifecycle.compose_down(runner,
                                                loader.dir(project_id),
                                                env=secrets_svc.values(project_id))
                if not result.ok:
                    raise JobFailed((result.stderr or result.stdout).strip()
                                    or "compose down failed")
                repos.projects.set_status(project_id, "stopped")
                return {"status": "stopped"}
            finally:
                locks.release(project_id)

        return {"job_id": submit_locked(project_id, work, "down")}

    def resume_projects() -> list[str]:
        """Projects carry no restart policy, so a VM reboot leaves them stopped
        while state.db still says started and Traefik answers 404."""
        job_ids = []
        for row in repos.projects.list():
            if row["status"] != STARTED_OK:
                continue
            try:
                job_ids.append(submit_locked(
                    row["id"], start_work(row["id"], stop_first=False), "up"))
            except EggieError as e:
                log.warning("not resuming %s: %s", row["id"], e.message)
        return job_ids

    app.state.resume_projects = resume_projects

    @router.get("/projects/{project_id}/logs")
    def project_logs(project_id: str, follow: bool = False,
                     service: str | None = None):
        loader.require(project_id)
        if not follow:
            result = lifecycle.project_logs(runner, loader.dir(project_id),
                                            service, env=secrets_svc.values(project_id))
            if not result.ok:
                raise Conflict("logs_unavailable",
                               (result.stderr or result.stdout).strip()
                               or "docker compose logs failed")
            return PlainTextResponse(result.stdout, media_type=TEXT)
        argv = lifecycle.logs_argv(loader.dir(project_id), service, follow=True)
        return StreamingResponse(
            runner.stream(argv, root=True, env=secrets_svc.values(project_id)),
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
