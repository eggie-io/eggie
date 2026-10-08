from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from .account import AccountRepo
from .cloud_projects import CloudProjectRepo
from .github import GitHubRepo
from .projects import ProjectRepo
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

    @classmethod
    def open(cls, db: Database) -> "Repos":
        return cls(db, ProjectRepo(db), SecretRepo(db), SessionRepo(db),
                   AccountRepo(db), GitHubRepo(db), CloudProjectRepo(db))
