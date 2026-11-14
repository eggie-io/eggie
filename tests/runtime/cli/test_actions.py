import pytest
import yaml

from eggie_api.infra.cloud import CloudError
from tests.runtime.api.conftest import AUTH, COMPOSE_ONE_WEB
from tests.runtime.api.fake_cloud import FakeCloud
from tests.runtime.api.fake_helper import FakeHelper
from tests.runtime.cli.conftest import _guest

ACTIONS = {"actions": [
    {"id": "ios-build", "name": "Build for iPhone", "description": None,
     "command": "eas build -p ios", "type": "long-running"},
    {"id": "login", "name": "Sign in to Expo", "description": None,
     "command": "eas login", "type": "one-off"}]}


@pytest.fixture
def actions_guest(tmp_path):
    cloud = FakeCloud()
    for g in _guest(tmp_path, cloud=cloud, actions_helper=FakeHelper()):
        g.cloud = cloud
        g.folder = g.root / "app"
        g.folder.mkdir()
        (g.folder / "docker-compose.yml").write_text(COMPOSE_ONE_WEB)
        g._client.post("/projects", json={"id": "app", "origin": "folder"}, headers=AUTH)
        yield g


def _recipes(guest, *slugs):
    (guest.folder / ".eggie").mkdir(exist_ok=True)
    (guest.folder / ".eggie/project.yml").write_text(yaml.safe_dump({"recipes": list(slugs)}))


def test_actions_prints_each_action_then_each_problem(actions_guest):
    _recipes(actions_guest, "mobile-app", "gone")
    actions_guest.cloud.replies["recipe_actions"] = [
        ACTIONS, CloudError("recipe_not_found", "m", 404)]
    code, out, err = actions_guest.run("actions", cwd=actions_guest.folder)
    assert (code, err) == (0, "")
    assert out == ("ios-build  long-running  Build for iPhone\n"
                   "login      one-off       Sign in to Expo\n"
                   "! There is no published recipe 'gone'.\n")


def test_actions_of_a_named_project_from_anywhere(actions_guest):
    _recipes(actions_guest, "mobile-app")
    actions_guest.cloud.replies["recipe_actions"] = [ACTIONS]
    code, out, _err = actions_guest.run("actions", "--project", "app", cwd=actions_guest.root)
    assert code == 0
    assert out.startswith("ios-build")


def test_a_project_without_actions_says_so(actions_guest):
    code, out, _err = actions_guest.run("actions", cwd=actions_guest.folder)
    assert (code, out) == (0, "No actions in app.\n")
