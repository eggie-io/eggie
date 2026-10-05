import io
import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from eggie_api.core.github import (GitHub, GitHubError, GitHubUnavailable,
                                    auth_failed, clone_argv, identity_from,
                                    redact, valid_repo)


class Reply:
    def __init__(self, body, status=200, headers=None):
        self.status = status
        self._raw = json.dumps(body).encode()
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def http_error(status, body):
    return urllib.error.HTTPError("https://x", status, "err", Message(),
                                  io.BytesIO(json.dumps(body).encode()))


def gh(*replies):
    opener = Opener(*replies)
    return GitHub("https://gh.test", "https://api.gh.test", opener=opener), opener


def test_a_device_token_error_sent_with_status_200_is_raised_with_its_code():
    client, _ = gh(Reply({"error": "slow_down", "interval": 10}))
    with pytest.raises(GitHubError) as caught:
        client.device_token("cid", "dc")
    assert (caught.value.code, caught.value.interval) == ("slow_down", 10)


def test_a_401_is_bad_credentials():
    client, _ = gh(http_error(401, {"message": "Bad credentials"}))
    with pytest.raises(GitHubError) as caught:
        client.user("tok")
    assert caught.value.code == "bad_credentials"


def test_a_network_failure_is_unavailable_not_an_error():
    client, _ = gh(OSError("no route"))
    with pytest.raises(GitHubUnavailable):
        client.user("tok")


def test_repos_reports_more_pages_from_the_link_header():
    page = [{"full_name": "octo/app", "private": True, "description": None,
             "updated_at": "2026-09-20T10:00:00Z"}]
    client, opener = gh(
        Reply(page, headers={"Link": '<https://api.gh.test/user/repos?page=2>; rel="next"'}),
        Reply(page))
    assert client.repos("tok", 1) == (page, True)
    assert client.repos("tok", 2) == (page, False)
    url = opener.requests[0].full_url
    assert "sort=updated" in url and "tok" not in url


def test_identity_falls_back_to_login_and_the_noreply_address():
    assert identity_from({"login": "octo", "id": 42, "name": "", "email": None}) == {
        "login": "octo", "gh_id": 42, "name": "octo",
        "email": "42+octo@users.noreply.github.com"}
    assert identity_from({"login": "octo", "id": 42, "name": "Octo Cat",
                          "email": "octo@example.com"})["email"] == "octo@example.com"


@pytest.mark.parametrize("name", ["../x", "a/b/c", "https://github.com/a/b",
                                  "a/..", "", "a b/c", "-x/y"])
def test_repo_names_that_are_not_owner_slash_name_are_refused(name):
    assert not valid_repo(name)


def test_clone_argv_keeps_the_token_out_of_argv_and_the_url():
    argv = clone_argv("octo/app", Path("/opt/eggie/projects/app"))
    assert "https://github.com/octo/app.git" in argv
    assert not any("@github.com" in word for word in argv)
    assert "$EGGIE_GH_TOKEN" in " ".join(argv)
    assert argv[-1] == "/opt/eggie/projects/app"


def test_redact_and_auth_failed():
    assert redact("x gho_abc y", "gho_abc") == "x [token] y"
    assert redact("nothing", "") == "nothing"
    assert auth_failed("fatal: Authentication failed for 'https://github.com/a/b.git/'")
    assert not auth_failed("fatal: repository 'x' not found")
