"""DesktopApi.inspect_folder's conflict answer.

"Replace" deletes a project outright, so the UI may only offer it when a
project genuinely exists. Every failure to find out must answer "no
conflict" -- an unreachable API must never put a destructive option in
front of the user.
"""
from __future__ import annotations

import pytest

from host.core.install import InstallState
from host.core.status import Readiness
from host.desktop.api import DesktopApi


class FakeProvider:
    pass


def _api(tmp_path):
    return DesktopApi(FakeProvider(), InstallState(tmp_path / "s.json"),
                      push=lambda event: None,
                      probe_fn=lambda provider: Readiness())


def _patch_client(monkeypatch, client):
    from host import client as client_module
    monkeypatch.setattr(client_module.ApiClient, "for_provider",
                        classmethod(lambda cls, provider, **kw: client))


def test_an_existing_project_is_reported_as_a_conflict(tmp_path, monkeypatch):
    folder = tmp_path / "recipe-box"
    folder.mkdir()

    class Existing:
        def get_project(self, project_id):
            return {"id": project_id}

    _patch_client(monkeypatch, Existing())
    assert _api(tmp_path).inspect_folder(str(folder))["conflict"] is True


@pytest.mark.parametrize("failure", [
    RuntimeError("could not reach the Eggie API"),
    OSError("connection refused"),
    ValueError("nonsense"),
])
def test_any_failure_to_ask_reports_no_conflict(tmp_path, monkeypatch, failure):
    """Fail closed toward the SAFE option. A conflict reported because the
    lookup broke would offer Replace, which deletes the project."""
    folder = tmp_path / "recipe-box"
    folder.mkdir()

    class Broken:
        def get_project(self, project_id):
            raise failure

    _patch_client(monkeypatch, Broken())
    assert _api(tmp_path).inspect_folder(str(folder))["conflict"] is False


def test_inspect_folder_carries_the_project_id_and_the_counts(tmp_path, monkeypatch):
    folder = tmp_path / "recipe-box"
    folder.mkdir()
    (folder / "a.txt").write_text("x" * 10)

    class Missing:
        def get_project(self, project_id):
            raise RuntimeError("404")

    _patch_client(monkeypatch, Missing())
    result = _api(tmp_path).inspect_folder(str(folder))

    from host.client import project_id_for
    assert result["project_id"] == project_id_for("recipe-box")
    assert (result["files"], result["bytes"]) == (1, 10)
    assert result["path"] == str(folder)
