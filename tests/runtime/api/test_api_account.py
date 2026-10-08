from fastapi.testclient import TestClient

from eggie_api.services.account import Account
from eggie_api.infra.cloud import CloudUnavailable
from eggie_api.infra.db import Database
from eggie_api.infra.repos import Repos
from eggie_api.http.app import create_app
from tests.runtime.api.conftest import AUTH, FakeRunner
from tests.runtime.api.fake_cloud import CODE, FakeCloud


def client(env, cloud):
    repos = Repos.open(Database(env.config.state_db.with_name("account.db")))
    account = Account(repos.account, repos.cloud_projects, cloud, spawn=lambda fn: None)
    app = create_app(config=env.config, runner=FakeRunner(), repos=repos,
                     account=account)
    return TestClient(app, headers=AUTH)


def test_sign_in_answers_with_the_code_to_show(env):
    resp = client(env, FakeCloud(device_code=[CODE])).post("/account/sign-in")

    assert resp.status_code == 200
    body = resp.json()
    assert (body["state"], body["user_code"]) == ("pending", "ABCD-EFGH")


def test_sign_in_says_when_the_service_cannot_be_reached(env):
    resp = client(env, FakeCloud(device_code=[CloudUnavailable("down")])).post("/account/sign-in")

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "cloud_unavailable"
