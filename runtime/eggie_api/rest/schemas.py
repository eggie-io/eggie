from __future__ import annotations

from pydantic import BaseModel, Field


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
