from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ApiConfig
from .infra import disk
from .infra.cloud import Cloud
from .infra.db import Database
from .infra.github import GitHub
from .infra.health import default_probe
from .infra.repos import Repos
from .infra.runner import LocalRunner
from .infra.tunnel import TunnelClient
from .infra.uploads import UploadStore
from .services.account import Account
from .services.agents import AgentStatus
from .services.clone import CloneService
from .services.files import FileService
from .services.github_link import GitHubLink
from .services.jobs import JobRegistry
from .services.lifecycle import LifecycleService
from .services.loader import ProjectLoader
from .services.locks import ProjectLocks
from .services.projects import ProjectService
from .services.public import Public
from .services.secrets import SecretService
from .services.sessions import Sessions
from .services.sync import SyncLoop, run_pass
from .services.system import SystemService
from .services.uploads import UploadService


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
    github_link: GitHubLink
    sessions: Sessions
    agents: AgentStatus
    sync: SyncLoop
    system: SystemService


def build(config: ApiConfig, *, runner=None, http_probe=None, cloud=None,
          github=None, account=None, public=None, github_link=None,
          sessions=None, jobs=None, repos=None) -> Services:
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
    account = account or Account(repos.account, repos.cloud_projects, cloud)
    public = public or Public(
        projects=repos.projects, cloud_projects=repos.cloud_projects,
        account=account, cloud=cloud,
        client=TunnelClient(runner, config.stack_file),
        token_path=config.tunnel_token_path,
        origin=f"http://{config.traefik_host}:{config.edge_port}",
        hosts_for=loader.public_hosts)
    account.on_forget = public.forget_local
    # Looked up at sign-out, not bound now: a public double without
    # release_all then fails there (caught and logged), not at build time.
    account.before_sign_out = lambda: public.release_all()
    sync = SyncLoop([public.reconcile, lambda: run_pass(account, cloud, repos)])
    account.on_signed_in = sync.wake
    github = github or GitHub(config.github_url, config.github_api_url)
    github_link = github_link or GitHubLink(
        repos.github, github, client_id=config.github_client_id,
        directory=config.github_dir)
    lifecycle = LifecycleService(config, runner, loader, repos.projects, secrets,
                                 jobs, locks, http_probe)
    return Services(
        config=config, repos=repos, runner=runner, jobs=jobs, locks=locks,
        loader=loader,
        projects=ProjectService(config, runner, loader, repos.projects, secrets,
                                jobs, locks, uploads, public, http_probe,
                                sync.wake),
        lifecycle=lifecycle,
        clone=CloneService(config, runner, loader, repos.projects, locks,
                           github_link, lifecycle, sync.wake),
        files=FileService(loader, locks), uploads=uploads, secrets=secrets,
        account=account, public=public, github_link=github_link,
        sessions=sessions,
        agents=AgentStatus(config.agent_status_dir, config.agents_dir),
        sync=sync, system=SystemService(config, runner))
