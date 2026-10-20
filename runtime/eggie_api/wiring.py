from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .config import ApiConfig
from .errors import Conflict
from .infra import disk
from .infra.cloud import Cloud
from .infra.db import Database
from .infra.github import GitHub
from .infra.health import default_probe
from .infra.kitchen import KitchenClient
from .infra.repos import Repos
from .infra.runner import LocalRunner
from .infra.tunnel import TunnelClient
from .infra.uploads import UploadStore
from .services.account import Account
from .services.agents import AgentStatus
from .services.certificate import Certificate
from .services.clone import CloneService
from .services.files import FileService
from .services.github_link import GitHubLink
from .services.instances import Instances
from .services.jobs import JobRegistry
from .services.lifecycle import LifecycleService
from .services.loader import ProjectLoader
from .services.locks import ProjectLocks
from .services.projects import ProjectService
from .services.public import Public
from .services.push import PushService
from .services.secrets import SecretService
from .services.sessions import Sessions
from .services.site import SiteSignIn
from .services.sync import SyncLoop, run_pass
from .services.system import SystemService
from .services.transfers import Transfers
from .services.uploads import UploadService


log = logging.getLogger("eggie.api")


def _start_quietly(lifecycle: LifecycleService, project_id: str) -> None:
    try:
        lifecycle.start(project_id)
    except Conflict as e:
        # The last secret is already saved; the user can still press Start.
        if e.code != "project_busy":
            raise
        log.info("not starting %s after its last secret: %s", project_id, e.message)


@dataclass(frozen=True)
class Services:
    config: ApiConfig
    repos: Repos
    runner: object
    jobs: JobRegistry
    locks: ProjectLocks
    loader: ProjectLoader
    projects: ProjectService
    lifecycle: LifecycleService
    clone: CloneService
    files: FileService
    uploads: UploadService
    secrets: SecretService
    account: Account
    public: Public
    push: PushService
    transfers: Transfers
    github_link: GitHubLink
    sessions: Sessions
    site: SiteSignIn
    instances: Instances
    agents: AgentStatus
    certificate: Certificate | None
    sync: SyncLoop
    system: SystemService


def build(config: ApiConfig, *, runner=None, http_probe=None, cloud=None,
          github=None, account=None, public=None, github_link=None,
          sessions=None, jobs=None, repos=None, kitchen=None,
          sleep=None, free_bytes=None) -> Services:
    """The one place that knows the whole graph. Keyword arguments are the
    fakes tests inject; production passes none."""
    runner = runner or LocalRunner()
    http_probe = http_probe or default_probe
    repos = repos if repos is not None else Repos.open(Database(config.state_db))
    jobs = jobs or JobRegistry()
    locks = ProjectLocks()
    loader = ProjectLoader(config, repos.projects)
    sessions = sessions if sessions is not None else Sessions(repos.sessions)
    store = UploadStore(config.uploads_root,
                        free_bytes=lambda: disk.usage(
                            Path(config.projects_root))["free_bytes"])
    store.sweep()
    uploads = UploadService(store, loader, locks)
    secrets = SecretService(repos.secrets, loader)
    cloud = cloud or Cloud(config.cloud_url)
    account = account or Account(repos.account, repos.cloud_projects, cloud,
                                 managed=config.site_host is not None)
    public = public or Public(
        projects=repos.projects, cloud_projects=repos.cloud_projects,
        account=account, cloud=cloud,
        client=TunnelClient(runner, config.stack_file),
        token_path=config.tunnel_token_path,
        origin=f"http://{config.traefik_host}:{config.edge_port}",
        hosts_for=loader.public_hosts,
        site=config.site_host is not None)
    site = SiteSignIn(sessions, account, cloud, remote=config.site_host is not None)
    account.on_forget = public.forget_local
    # Looked up at sign-out, not bound now: a public double without
    # release_all then fails there (caught and logged), not at build time.
    account.before_sign_out = lambda: public.release_all()
    certificate = None
    if config.site_host:
        certificate = Certificate(
            config.tls_dir, account, cloud,
            names=(config.site_host, config.domain, f"*.{config.domain}"))
    passes = [public.reconcile, lambda: run_pass(account, cloud, repos)]
    if certificate is not None:
        passes.append(certificate.refresh)
    sync = SyncLoop(passes)
    account.on_signed_in = sync.wake
    github = github or GitHub(config.github_url, config.github_api_url)
    github_link = github_link or GitHubLink(
        repos.github, github, client_id=config.github_client_id,
        directory=config.github_dir)
    lifecycle = LifecycleService(config, runner, loader, repos.projects, secrets,
                                 jobs, locks, http_probe)
    secrets.on_all_set = lambda pid: _start_quietly(lifecycle, pid)
    push = PushService(
        config, runner, loader, repos.projects, secrets, lifecycle, locks, account, cloud,
        kitchen or KitchenClient(ca_file=config.kitchen_ca_file), repos.cloud_projects,
        repos.kitchen_pushes, sync.wake, sleep=sleep or time.sleep)
    push.sweep()
    transfers = Transfers(
        config, runner, loader, repos.projects, repos.pushed_projects, secrets, lifecycle,
        jobs, locks, account, cloud,
        free_bytes=free_bytes or (lambda: disk.usage(
            Path(config.transfers_root))["free_bytes"]),
        sleep=sleep or time.sleep)
    transfers.sweep()
    return Services(
        config=config, repos=repos, runner=runner, jobs=jobs, locks=locks,
        loader=loader,
        projects=ProjectService(config, runner, loader, repos.projects, secrets,
                                jobs, locks, uploads, public, http_probe,
                                sync.wake, push.summary, repos.pushed_projects),
        lifecycle=lifecycle,
        clone=CloneService(config, runner, loader, repos.projects, locks,
                           github_link, lifecycle, sync.wake),
        files=FileService(loader, locks), uploads=uploads, secrets=secrets,
        account=account, public=public, push=push, transfers=transfers,
        github_link=github_link,
        sessions=sessions, site=site,
        instances=Instances(account, cloud, config.site_host),
        agents=AgentStatus(config.agent_status_dir, config.agents_dir),
        certificate=certificate, sync=sync, system=SystemService(config, runner))
