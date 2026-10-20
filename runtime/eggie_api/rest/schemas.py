from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WebOverride(BaseModel):
    service: str
    port: int
    subdomain: str | None = None


class CreateProject(BaseModel):
    id: str
    web: list[WebOverride] | None = None
    domain: str | None = None
    origin: Literal["new", "folder"] = "new"


class StartUpload(BaseModel):
    path: str
    size: int = Field(ge=0)
    fingerprint: str = ""
    replace: bool = False


class Handoff(BaseModel):
    code: str = Field(min_length=1, max_length=256)


class SecretValue(BaseModel):
    value: str


class SecretHint(BaseModel):
    hint: str


class CloneRepo(BaseModel):
    repo: str
    id: str | None = None
