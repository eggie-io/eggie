from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from .account import AccountRepo
from .cloud_projects import CloudProjectRepo
from .github import GitHubRepo
from .kitchen_pushes import KitchenPushRepo
from .projects import ProjectRepo
from .pushed_projects import PushedProjectRepo
from .secrets import SecretRepo
from .sessions import SessionRepo


@dataclass(frozen=True)
class Repos:
    db: Database
    projects: ProjectRepo
    secrets: SecretRepo
    sessions: SessionRepo
    account: AccountRepo
    github: GitHubRepo
    cloud_projects: CloudProjectRepo
    kitchen_pushes: KitchenPushRepo
    pushed_projects: PushedProjectRepo

    @classmethod
    def open(cls, db: Database) -> "Repos":
        return cls(db, ProjectRepo(db), SecretRepo(db), SessionRepo(db),
                   AccountRepo(db), GitHubRepo(db), CloudProjectRepo(db),
                   KitchenPushRepo(db), PushedProjectRepo(db))
