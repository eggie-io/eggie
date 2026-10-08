from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCOPES = "repo read:org workflow"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_REPO = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*/[A-Za-z0-9_.][A-Za-z0-9_.-]*")
# The helper reads the token from the child's environment at call time, so it
# is never in argv, the remote URL or .git/config.
_HELPER = ('!f() { test "$1" = get && printf "username=x-access-token\\npassword=%s\\n" '
           '"$EGGIE_GH_TOKEN"; }; f')


class GitHubError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 0,
                 interval: float | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.interval = interval


class GitHubUnavailable(Exception):
    pass


class GitHub:
    def __init__(self, web_url: str, api_url: str, *, opener=None,
                 timeout: float = 10.0):
        self._web = web_url.rstrip("/")
        self._api = api_url.rstrip("/")
        self._open = opener or urllib.request.urlopen
        self._timeout = timeout

    def _call(self, method: str, url: str, *, form: dict | None = None,
              token: str | None = None):
        data = None if form is None else urllib.parse.urlencode(form).encode()
        headers = {"Accept": "application/json", "User-Agent": "eggie",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers,
                                         method=method)
        try:
            with self._open(request, timeout=self._timeout) as response:
                raw, link = response.read(), response.headers.get("Link", "")
        except urllib.error.HTTPError as e:
            body = _json(e.read())
            message = str(body.get("message", "")) if isinstance(body, dict) else ""
            code = "bad_credentials" if e.code == 401 else f"http_{e.code}"
            raise GitHubError(code, message, e.code) from None
        except (OSError, http.client.HTTPException) as e:
            raise GitHubUnavailable(str(e)) from None
        body = _json(raw)
        # The device endpoints report pending/denied/expired as 200 + "error".
        if isinstance(body, dict) and "error" in body:
            interval = body.get("interval")
            raise GitHubError(str(body["error"]),
                              str(body.get("error_description", "")), 200,
                              float(interval) if interval else None)
        return body, link

    def device_code(self, client_id: str) -> dict:
        body, _ = self._call("POST", f"{self._web}/login/device/code",
                             form={"client_id": client_id, "scope": SCOPES})
        return body

    def device_token(self, client_id: str, device_code: str) -> dict:
        body, _ = self._call("POST", f"{self._web}/login/oauth/access_token",
                             form={"client_id": client_id,
                                   "device_code": device_code,
                                   "grant_type": DEVICE_GRANT})
        return body

    def user(self, token: str) -> dict:
        body, _ = self._call("GET", f"{self._api}/user", token=token)
        return body

    def repos(self, token: str, page: int) -> tuple[list[dict], bool]:
        query = urllib.parse.urlencode({
            "sort": "updated", "per_page": 50, "page": page,
            "affiliation": "owner,collaborator,organization_member"})
        body, link = self._call("GET", f"{self._api}/user/repos?{query}",
                                token=token)
        return body, 'rel="next"' in link


def _json(raw: bytes):
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        raise GitHubError("bad_response",
                          "GitHub sent something that is not JSON") from None


def identity_from(user: dict) -> dict:
    login = user["login"]
    return {"login": login, "gh_id": user["id"],
            "name": (user.get("name") or "").strip() or login,
            "email": ((user.get("email") or "").strip()
                      or f"{user['id']}+{login}@users.noreply.github.com")}


def valid_repo(full_name: str) -> bool:
    return (bool(_REPO.fullmatch(full_name))
            and ".." not in full_name.split("/"))


def clone_argv(full_name: str, dest: Path) -> list[str]:
    # umask 002 + sharedRepository=group: the agent's account is root on WSL2
    # and the macOS uid on Lima, never this API's uid 1000 -- it writes
    # through the docker group the projects folder hands down.
    return ["sh", "-c", 'umask 002 && exec "$@"', "sh",
            "git", "-c", "credential.helper=",
            "-c", f"credential.https://github.com.helper={_HELPER}",
            "-c", "core.sharedRepository=group",
            "clone", "--", f"https://github.com/{full_name}.git", str(dest)]


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "[token]") if secret else text


def auth_failed(git_output: str) -> bool:
    lowered = git_output.lower()
    return ("authentication failed" in lowered
            or "could not read username" in lowered
            or "error: 403" in lowered)
