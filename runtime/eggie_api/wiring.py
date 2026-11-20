from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path

from .config import ApiConfig
from .domain.edge import ProjectEdge
from .domain.overlay import host_for
from .errors import EggieError
from .infra import connect, disk
from .infra.cloud import Cloud
from .infra.db import Database
from .infra.github import GitHub
from .infra.health import default_probe
from .infra.instance import InstanceClient
from .infra.repos import Repos
from .infra.runner import LocalRunner
from .infra.tunnel import TunnelClient
from .infra.uploads import UploadStore
from .services.account import Account
from .services.agents import AgentStatus
from .services.certificates import Certificates
from .services.clone import CloneService
from .services.domains import Domains
from .services.edge import Edge
from .services.files import FileService
from .services.github_link import GitHubLink
from .services.instance_projects import InstanceProjects
from .services.instances import Instances
from .services.jobs import JobRegistry
from .services.lifecycle import LifecycleService
from .services.loader import ProjectLoader
from .services.locks import ProjectLocks
from .services.projects import ProjectService
from .services.public import Public
from .services.recipes import RecipeService
from .services.runtime_update import RuntimeUpdate
from .services.push import PushService
from .services.secrets import SecretService
from .services.sessions import Sessions
from .services.site import SiteSignIn
from .services.sync import SyncLoop, run_pass
from .services.system import SystemService
from .services.transfers import Transfers
from .services.uploads import UploadService


log = logging.getLogger("eggie.api")


def _project_edges(loader: ProjectLoader, projects) -> Callable[[Collection[str]], dict]:
    def edges(ids: Collection[str]) -> dict[str, ProjectEdge]:
        out = {}
        for pid in ids:
            row = projects.get(pid)
            if row is None:
                continue
            try:
                webs = loader.load(pid).webs
            except EggieError:
                continue
            if webs:
                out[pid] = ProjectEdge(host_for(pid, webs[0], row["domain"]),
                                       f"{pid}--{webs[0].service}")
        return out
    return edges


def _start_quietly(lifecycle: LifecycleService, project_id: str) -> None:
    try:
        lifecycle.start(project_id)
    except EggieError as e:
        # The last secret is already saved; the user can still press Start.
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
    recipes: RecipeService
    transfers: Transfers
    github_link: GitHubLink
    sessions: Sessions
    site: SiteSignIn
    instances: Instances
    agents: AgentStatus
    runtime_update: RuntimeUpdate
    certificates: Certificates | None
    domains: Domains | None
    edge: Edge | None
    sync: SyncLoop
    system: SystemService


def build(config: ApiConfig, *, runner=None, http_probe=None, cloud=None,
          github=None, account=None, public=None, github_link=None,
          sessions=None, jobs=None, repos=None, instance=None,
          sleep=None, clock=None, free_bytes=None, timer=None) -> Services:
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
    # Read per call: install.sh records the VM kind after the api first starts.
    cloud = cloud or Cloud(config.cloud_url, user_agent=lambda: (
        f"Eggie/{config.version} ({connect.host_os(config.connect_path)})"))
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
    certificates = edge = domains = None
    if config.site_host:
        certificates = Certificates(config.edge_dir, account, cloud)

        def domains_changed() -> None:
            # `edge` and `sync` are bound below, before anything can call this.
            try:
                edge.write()
            except Exception:
                log.exception("writing the edge file failed")
            sync.wake()

        domains = Domains(repos.domains, loader, account, cloud, config,
                          installed=lambda: {p.id for p in certificates.installed()},
                          changed=domains_changed)
        edge = Edge(config.edge_dir, certificates, domains.rows,
                    _project_edges(loader, repos.projects))
        loader.primary_url = domains.primary_url
    passes = [public.reconcile, lambda: run_pass(account, cloud, repos)]
    if edge is not None:
        def edge_pass() -> None:
            # Each step on its own: certificates and the file still follow a failed domain sync.
            for step in (domains.sync,
                         lambda: certificates.refresh(domains.certificate_ids()),
                         edge.write):
                try:
                    step()
                except Exception:
                    log.exception("edge pass step %s failed",
                                  getattr(step, "__qualname__", step))
        passes.append(edge_pass)
    if config.site_host:
        passes.append(InstanceProjects(repos.projects, loader, account, cloud).report)
    sync = SyncLoop(passes)
    account.on_signed_in = sync.wake
    github = github or GitHub(config.github_url, config.github_api_url)
    github_link = github_link or GitHubLink(
        repos.github, github, client_id=config.github_client_id,
        directory=config.github_dir)
    instances = Instances(account, cloud, config.site_host)
    lifecycle = LifecycleService(config, runner, loader, repos.projects, secrets,
                                 jobs, locks, http_probe)
    secrets.on_all_set = lambda pid: _start_quietly(lifecycle, pid)
    lifecycle.on_status = sync.wake
    push = PushService(
        config, runner, loader, repos.projects, secrets, lifecycle, locks, account, cloud,
        instance or InstanceClient(ca_file=config.instance_ca_file), repos.cloud_projects,
        repos.instance_pushes, sync.wake, clock=clock or time.time,
        sleep=sleep or time.sleep)
    push.sweep()
    transfers = Transfers(
        config, runner, loader, repos.projects, repos.pushed_projects, secrets, lifecycle,
        jobs, locks, account, cloud,
        free_bytes=free_bytes or (lambda: disk.usage(
            Path(config.transfers_root))["free_bytes"]),
        sleep=sleep or time.sleep, timer=timer)
    transfers.sweep()
    domain_hooks = {} if domains is None else {
        "domains_for": domains.summary, "forget_domains": domains.forget_project}
    return Services(
        config=config, repos=repos, runner=runner, jobs=jobs, locks=locks,
        loader=loader,
        projects=ProjectService(config, runner, loader, repos.projects, secrets,
                                jobs, locks, uploads, public, http_probe,
                                sync.wake, push.summary, repos.pushed_projects,
                                **domain_hooks),
        lifecycle=lifecycle,
        clone=CloneService(config, runner, loader, repos.projects, locks,
                           github_link, lifecycle, sync.wake),
        files=FileService(loader, locks), uploads=uploads, secrets=secrets,
        account=account, public=public, push=push, transfers=transfers,
        recipes=RecipeService(config, account, cloud),
        github_link=github_link,
        sessions=sessions, site=site,
        instances=instances,
        agents=AgentStatus(config.agent_status_dir, config.agents_dir),
        runtime_update=RuntimeUpdate(config.update_dir, config.runtime_marker),
        certificates=certificates, domains=domains, edge=edge, sync=sync, system=SystemService(config, runner, site_login=instances.own_login))
