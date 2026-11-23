import pytest
from fastapi.testclient import TestClient

from eggie_api.infra.cloud import CloudError, CloudUnavailable
from tests.runtime.api.conftest import AUTH, _make_env
from tests.runtime.api.fake_cloud import FakeCloud

ROW = {"id": "i1", "name": "foo", "display_name": "Foo", "login_name": "ada",
       "console_hostname": "foo.eggie.io", "status": "running", "is_owner": True,
       "ipv4": "1.2.3.4"}
ASK = {"public_key": "ssh-ed25519 AAAA", "computer_name": "Mac"}


@pytest.fixture
def make(tmp_path):
    gens = []

    def _make(cloud, *, signed_in=True, **overrides):
        gen = _make_env(tmp_path, cloud=cloud, **overrides)
        gens.append(gen)
        env = next(gen)
        if signed_in:
            env.repos.account.update(access_token="at", refresh_token="rt",
                                     access_expires_at=10**12, org_id="org-1")
        return env

    yield _make
    for gen in gens:
        gen.close()


def test_list_returns_login_and_name_for_the_host(make):
    broken = {**ROW, "id": "i2", "login_name": ""}
    env = make(FakeCloud(list_instances=[[ROW, broken]]))

    out = env.client.get("/instances")

    assert out.json() == {"instances": [{
        "id": "i1", "name": "foo", "display_name": "Foo", "login_name": "ada",
        "console_hostname": "foo.eggie.io", "status": "running", "is_owner": True}]}


def test_request_passes_the_service_conflict_through(make):
    cloud = FakeCloud(request_instance_key=[
        CloudError("ssh_key_installed", "already there", 409)])
    env = make(cloud)

    out = env.client.post("/instances/i1/ssh-key-requests", json=ASK)

    assert out.status_code == 409
    assert out.json()["error"]["code"] == "ssh_key_installed"
    assert cloud.calls == [("request_instance_key", "at", "i1", "ssh-ed25519 AAAA", "Mac")]


def test_a_created_request_is_a_201_and_a_removal_a_204(make):
    cloud = FakeCloud(request_instance_key=[{"id": "r1", "status": "pending"}],
                      remove_instance_key=[None])
    env = make(cloud)

    assert env.client.post("/instances/i1/ssh-key-requests", json=ASK).status_code == 201
    assert env.client.delete("/instances/i1/ssh-keys",
                             params={"fingerprint": "SHA256:a/b"}).status_code == 204
    assert cloud.calls[-1] == ("remove_instance_key", "at", "i1", "SHA256:a/b")


def test_not_signed_in_is_409_not_signed_in(make):
    env = make(FakeCloud(), signed_in=False)

    out = env.client.get("/instances/i1/ssh-key-requests/r1")

    assert (out.status_code, out.json()["error"]["code"]) == (409, "not_signed_in")


@pytest.mark.parametrize("failure, status, code", [
    (CloudUnavailable("down"), 503, "cloud_unavailable"),
    (CloudError("instance_forbidden", "no", 403), 403, "instance_forbidden"),
    (CloudError("ssh_key_request_not_found", "no", 404), 404, "ssh_key_request_not_found"),
    (CloudError("invalid_ssh_key", "bad", 422), 422, "invalid_ssh_key"),
    (CloudError("ssh_key_request_rate_limited", "slow", 429, 60), 429,
     "ssh_key_request_rate_limited"),
    (CloudError("boom", "x", 500), 502, "boom"),
])
def test_service_failures_keep_their_code_and_map_to_a_status(make, failure, status, code):
    env = make(FakeCloud(instance_key_request=[failure]))

    out = env.client.get("/instances/i1/ssh-key-requests/r1")

    assert (out.status_code, out.json()["error"]["code"]) == (status, code)


def test_routes_absent_on_a_site(make):
    env = make(FakeCloud(), site_host="foo.eggie.io", domain="foo.eggie.space")

    assert env.client.post("/instances/x/ssh-key-requests", json=ASK).status_code == 404


def test_routes_need_the_bearer_token(make):
    env = make(FakeCloud())

    assert TestClient(env.app).post("/instances/x/ssh-key-requests",
                                    json=ASK).status_code == 401
