from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path

from ..config import ApiConfig
from ..errors import Conflict, Invalid, NotFound, Unavailable, Upstream
from ..infra.cloud import CloudError, CloudUnavailable
from ..infra.recipe_zip import RECIPE_MD, BadBundle, unpack
from .account import NotSignedIn

MAX_BUNDLE_BYTES = 100 * 1024 ** 2
MAX_FILES = 10_000
MAX_UNPACKED_BYTES = 500 * 1024 ** 2
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _bad(reason: str) -> Upstream:
    return Upstream("bad_recipe_bundle", reason)


class RecipeService:
    """Recipes from the Eggie service, unpacked under `<projects_root>/.recipes`
    (dot-prefixed, so project discovery never adopts it) for the agent to follow."""

    def __init__(self, config: ApiConfig, account, cloud):
        self._root = Path(config.projects_root) / ".recipes"
        self._account = account
        self._cloud = cloud

    def _ask(self, fn):
        # Recipes are public; a signed-in VM still sends its token so the
        # service can tell paid recipes apart later.
        try:
            if self._account.signed_in:
                try:
                    return self._account.authed(fn)
                except NotSignedIn:
                    pass
            return fn(None)
        except CloudUnavailable:
            raise Unavailable("cloud_unavailable", "the Eggie service is unreachable")
        except CloudError as e:
            if e.status == 404:
                raise
            raise Upstream("cloud_error", e.message)

    def list(self) -> dict:
        out = self._ask(lambda token: self._cloud.recipes(token))
        items = out.get("items") if isinstance(out, dict) else None
        return {"recipes": [
            {"slug": r["slug"], "title": r.get("title"), "summary": r["summary"]}
            for r in items or []
            if isinstance(r, dict) and isinstance(r.get("slug"), str)
            and isinstance(r.get("summary"), str) and r["summary"].strip()]}

    def get(self, slug: str) -> dict:
        if len(slug) > 63 or not _SLUG.match(slug):
            raise Invalid("invalid_recipe", f"'{slug}' is not a recipe name")
        try:
            meta = self._ask(lambda token: self._cloud.recipe(token, slug))
        except CloudError:
            raise NotFound("recipe_not_found", f"There is no published recipe '{slug}'.")
        bundle = meta.get("bundle") if isinstance(meta, dict) else None
        if bundle is None:
            raise Conflict("recipe_has_no_bundle", "This recipe has nothing to download.")
        version, digest, size = self._check(bundle)
        target = self._root / slug / str(version)
        if not (target / RECIPE_MD).is_file():
            self._fetch(slug, version, digest, size, target)
        return {"slug": slug, "version": version, "path": str(target),
                "recipe_md": str(target / RECIPE_MD),
                "instructions": (target / RECIPE_MD).read_text(errors="replace")}

    @staticmethod
    def _check(bundle) -> tuple[int, str, int]:
        if not isinstance(bundle, dict):
            raise _bad("the Eggie service described this recipe's download oddly")
        version, digest, size = (bundle.get("version"), bundle.get("sha256"),
                                 bundle.get("size_bytes"))
        # bool is an int subclass; a JSON true is no version.
        if type(version) is not int or version < 1:
            raise _bad("the Eggie service gave this recipe no usable version")
        if not isinstance(digest, str) or not _SHA256.match(digest):
            raise _bad("the Eggie service gave this recipe no usable checksum")
        if type(size) is not int or size < 0:
            raise _bad("the Eggie service gave this recipe no usable size")
        if size > MAX_BUNDLE_BYTES:
            raise _bad(f"the recipe is larger than {MAX_BUNDLE_BYTES // 1024 ** 2} MB")
        return version, digest, size

    def _fetch(self, slug: str, version: int, digest: str, size: int,
               target: Path) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._root / f".tmp-{uuid.uuid4().hex[:12]}"
        tmp.mkdir()
        try:
            zip_path = tmp / "bundle.zip"
            try:
                self._ask(lambda token: self._cloud.recipe_bundle(
                    token, slug, version, zip_path, max_bytes=size))
            except CloudError as e:
                raise Upstream("cloud_error", e.message)
            with open(zip_path, "rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() != digest:
                    raise _bad("the recipe download did not match its checksum")
            out = tmp / "out"
            try:
                unpack(zip_path, out, max_files=MAX_FILES,
                       max_unpacked_bytes=MAX_UNPACKED_BYTES)
            except BadBundle as e:
                raise _bad(str(e))
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(out, target)
            except OSError:
                # Another get of the same version won the race; theirs is identical.
                if not (target / RECIPE_MD).is_file():
                    raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
