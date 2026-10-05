import shutil

import eggie_api.core.reconcile as reconcile
from eggie_api.core import constants
from tests.runtime.api.conftest import COMPOSE_ONE_WEB, _create, _write_compose


def _folder(env, name, compose=True):
    d = env.config.projects_root / name
    d.mkdir(parents=True, exist_ok=True)
    if compose:
        (d / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
    return d


def test_a_folder_the_coding_agent_made_is_listed_as_discovered(env):
    _folder(env, "invoice-helper")
    listing = env.client.get("/projects").json()
    assert [d["name"] for d in listing["discovered"]] == ["invoice-helper"]
    assert listing["discovered"][0]["adoptable"] is True


def test_registered_hidden_and_staging_entries_are_not_discovered(env):
    _create(env, "blog")
    _folder(env, ".cache")
    (env.config.projects_root / "tmpab12.upload").write_bytes(b"partial")
    assert env.client.get("/projects").json()["discovered"] == []


def test_the_setup_smoke_test_folder_is_never_discovered(env):
    # The installer deletes it without purge, so the folder itself outlives
    # the row -- it must not resurface as something a user can adopt.
    _folder(env, constants.VERIFY_PROJECT_ID)
    assert env.client.get("/projects").json()["discovered"] == []


def test_a_folder_without_compose_cannot_be_adopted(env):
    _folder(env, "spice-rack", compose=False)
    found = env.client.get("/projects").json()["discovered"][0]
    assert (found["adoptable"], found["reason"]) == (False, "compose_missing")
    resp = env.client.post("/projects/spice-rack/adopt")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_adoptable"


def test_a_folder_whose_name_is_not_a_project_id_cannot_be_adopted(env):
    # Adopting "My App" would register an id compose and Traefik disagree on.
    _folder(env, "My App")
    found = env.client.get("/projects").json()["discovered"][0]
    assert found["reason"] == "bad_name"


def test_adopt_registers_without_starting(env):
    _folder(env, "invoice-helper")
    resp = env.client.post("/projects/invoice-helper/adopt")
    assert resp.status_code == 201
    assert resp.json()["status"] == "stopped"
    assert not env.runner.argv_containing("up")
    assert env.client.get("/projects").json()["discovered"] == []


def test_a_project_whose_folder_was_removed_reports_folder_missing(env):
    _create(env, "blog")
    _write_compose(env, "blog")
    shutil.rmtree(env.config.projects_root / "blog")
    project = env.client.get("/projects").json()["projects"][0]
    assert project["problem"]["code"] == "folder_missing"


def test_a_new_empty_project_reads_as_empty_not_broken(env):
    _create(env, "blog")
    project = env.client.get("/projects/blog").json()
    assert project["empty"] is True
    assert project["problem"]["code"] == "compose_missing"


def test_a_folder_removed_mid_discovery_is_skipped_not_a_500(env, monkeypatch):
    _create(env, "blog")
    _folder(env, "ok-folder")
    _folder(env, "vanishing-folder")
    real_examine = reconcile.examine

    def flaky_examine(folder):
        if folder.name == "vanishing-folder":
            raise FileNotFoundError(folder)
        return real_examine(folder)

    monkeypatch.setattr(reconcile, "examine", flaky_examine)
    resp = env.client.get("/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["id"] for p in body["projects"]] == ["blog"]
    assert [d["name"] for d in body["discovered"]] == ["ok-folder"]
