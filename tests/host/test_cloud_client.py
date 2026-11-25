import io
import json
import urllib.error

import pytest

import host.cli as cli
from host.client import ApiError, ApiUnavailableError
from host.core.cloud.account import CloudAccount, NotSignedIn
from host.core.cloud.client import (CloudClient, http_call, service_api_url,
                                    web_app_url)
from typer.testing import CliRunner


class Opener:
    def __init__(self, *, status=200, body=b"{}", exc=None):
        self.status, self.body, self.exc, self.requests = status, body, exc, []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        if self.status >= 400:
            raise urllib.error.HTTPError(request.full_url, self.status, "x", {},
                                         io.BytesIO(self.body))
        return io.BytesIO(self.body)


def call_with(**kw):
    opener = Opener(**kw)
    return http_call("https://svc.test/api", opener=opener), opener


def test_error_body_becomes_api_error():
    body = json.dumps({"error": {"code": "ssh_key_installed", "message": "Already."}}).encode()
    call, _ = call_with(status=409, body=body)
    with pytest.raises(ApiError) as e:
        call("POST", "/v1/x", {}, "t")
    assert (e.value.api_code, e.value.status, e.value.message) == ("ssh_key_installed", 409, "Already.")


def test_html_error_page_still_has_a_code():
    call, _ = call_with(status=502, body=b"<html>Bad gateway</html>")
    with pytest.raises(ApiError) as e:
        call("GET", "/v1/x", None, None)
    assert (e.value.api_code, e.value.status) == ("cloud_error", 502)
    assert "502" in e.value.message


def test_no_connection_is_unavailable():
    call, _ = call_with(exc=urllib.error.URLError("refused"))
    with pytest.raises(ApiUnavailableError):
        call("GET", "/v1/x", None, None)


def test_request_carries_bearer_and_json_body():
    call, opener = call_with(body=b'{"ok": true}')
    assert call("POST", "/v1/x", {"a": 1}, "tok") == {"ok": True}
    req = opener.requests[0]
    assert req.full_url == "https://svc.test/api/v1/x"
    assert req.get_header("Authorization") == "Bearer tok"
    assert json.loads(req.data) == {"a": 1}


def test_env_overrides_service_and_web_urls():
    assert service_api_url({"EGGIE_SERVICE_API_URL": "http://x/api/"}) == "http://x/api"
    assert service_api_url({}) == "https://app.eggie.io/api"
    assert web_app_url({"EGGIE_WEB_APP_URL": "http://w"}) == "http://w"
    assert web_app_url({}) == "https://app.eggie.io"


def signed_in(tmp_path, reply):
    path = tmp_path / "account.json"
    path.write_text(json.dumps({"device_id": "d", "access_token": "a", "refresh_token": "r",
                                "access_expires_at": 9e12, "email": ""}))
    seen = []

    def call(method, p, body, token):
        seen.append((method, p, token))
        if isinstance(reply, Exception):
            raise reply
        return reply

    account = CloudAccount(path, call=call)
    return account, CloudClient(account, call), seen, path


class Service:
    """One access token per device, as the service has it: a refresh retires the
    old access token at once, and a refresh token works once."""

    def __init__(self, access="a", refresh="r"):
        self.access, self.refresh, self.n = access, refresh, 0
        self.refreshes = []
        self.before_refresh = lambda: None
        self.before_get = lambda: None

    def rotate(self):
        self.n += 1
        self.access, self.refresh = f"a{self.n}", f"r{self.n}"

    def __call__(self, method, path, body, token):
        if path == "/v1/auth/token/refresh":
            self.refreshes.append(body["refresh_token"])
            self.before_refresh()
            if body["refresh_token"] != self.refresh:
                raise ApiError("invalid_token", "dead", 401)
            self.rotate()
            return {"access_token": self.access, "refresh_token": self.refresh,
                    "expires_in": 3600}
        self.before_get()
        if token != self.access:
            raise ApiError("invalid_token", "stale", 401)
        return [{"id": "1", "token": token}]


def account_on(path, service, *, expires_at=9e12):
    path.write_text(json.dumps({"device_id": "d", "access_token": service.access,
                                "refresh_token": service.refresh,
                                "access_expires_at": expires_at, "email": ""}))
    return CloudAccount(path, call=service, clock=lambda: 1000.0)


def test_a_token_retired_by_a_refresh_elsewhere_retries_with_the_new_one(tmp_path):
    path = tmp_path / "account.json"
    service = Service()
    account = account_on(path, service)

    def refresh_while_in_flight():
        service.before_get = lambda: None
        account.renew("a")
    service.before_get = refresh_while_in_flight

    assert CloudClient(account, service).instances() == [{"id": "1", "token": "a1"}]
    assert service.refreshes == ["r"]
    assert json.loads(path.read_text())["refresh_token"] == "r1"


def test_a_rejected_token_with_nothing_newer_refreshes_once_and_retries(tmp_path):
    service = Service()
    account = account_on(tmp_path / "account.json", service)
    service.access = "revoked-by-the-service"

    assert CloudClient(account, service).instances() == [{"id": "1", "token": "a1"}]
    assert service.refreshes == ["r"]


def test_only_a_dead_refresh_token_signs_out(tmp_path):
    service = Service()
    path = tmp_path / "account.json"
    account = account_on(path, service)
    service.access, service.refresh = "gone", "gone"

    with pytest.raises(NotSignedIn):
        CloudClient(account, service).instances()
    assert not path.exists()


def test_the_cli_refreshing_first_leaves_the_app_signed_in(tmp_path):
    path = tmp_path / "account.json"
    service = Service()
    app = account_on(path, service, expires_at=1000.0)
    cli = CloudAccount(path, call=service, clock=lambda: 1000.0)
    # Both read r; the CLI's refresh lands first and the app's then spends a dead token.

    def cli_wins():
        service.before_refresh = lambda: None
        assert cli.access_token() == "a1"
    service.before_refresh = cli_wins

    assert CloudClient(app, service).instances() == [{"id": "1", "token": "a1"}]
    assert service.refreshes == ["r", "r"]
    assert json.loads(path.read_text())["refresh_token"] == "r1"


def test_a_reply_that_is_not_json_is_unavailable_not_a_crash():
    call, _ = call_with(body=b"<html>Sign in to the hotel wifi</html>")
    with pytest.raises(ApiUnavailableError):
        call("GET", "/v1/instances", None, "t")


def test_other_errors_keep_the_account(tmp_path):
    _, client, _, path = signed_in(tmp_path, ApiError("forbidden", "no", 403))
    with pytest.raises(ApiError):
        client.instances()
    assert path.exists()


@pytest.mark.parametrize("reply", [[{"id": "1"}], {"instances": [{"id": "1"}]}])
def test_instances_accepts_list_or_wrapper(tmp_path, reply):
    _, client, seen, _ = signed_in(tmp_path, reply)
    assert client.instances() == [{"id": "1"}]
    assert seen == [("GET", "/v1/instances", "a")]


def test_connect_without_sign_in_still_connects_this_computer(tmp_path, monkeypatch):
    from host.core.connect.flow import Connector

    class Provider:
        local_ssh_alias = None
        connected: list = []

        def connect_agent(self, agent_id):
            self.connected.append(agent_id)
            return "ok"

    provider = Provider()
    monkeypatch.setattr(
        cli, "_connector_factory",
        lambda say: Connector(lambda: _signed_out(tmp_path), provider, home=tmp_path,
                              env={}, computer_name="pc", say=say))
    result = CliRunner().invoke(cli.app, ["connect", "--codex"])
    assert result.exit_code == 1
    assert "\u2717 Kitchens: Open the Eggie app and sign in first." in result.output
    assert provider.connected == ["codex"]


def _signed_out(tmp_path):
    def call(*a):
        raise AssertionError("no network without a session")
    return CloudClient(CloudAccount(tmp_path / "account.json", call=call), call)
