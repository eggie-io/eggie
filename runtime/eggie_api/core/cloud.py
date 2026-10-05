from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

# A proxy's own error page, not the service: the service always answers with
# its JSON error body.
_GATEWAY = {502, 503, 504}


class CloudError(Exception):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


class CloudUnavailable(Exception):
    pass


def _error_from(status: int, raw: bytes) -> Exception:
    try:
        error = json.loads(raw)["error"]
        return CloudError(str(error["code"]), str(error["message"]), status)
    except (ValueError, KeyError, TypeError):
        if status in _GATEWAY:
            return CloudUnavailable(f"the Eggie service answered {status}")
        text = raw.decode("utf-8", errors="replace").strip()[:200]
        return CloudError(f"http_{status}", text or f"status {status}", status)


class Cloud:
    def __init__(self, base_url: str, *, opener=None, timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._open = opener or urllib.request.urlopen
        self._timeout = timeout

    def call(self, method: str, path: str, *, body: dict | None = None,
             token: str | None = None):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self._base + path, data=data,
                                         headers=headers, method=method)
        try:
            with self._open(request, timeout=self._timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as e:
            raise _error_from(e.code, e.read()) from None
        except (OSError, http.client.HTTPException) as e:
            # A truncated body (e.g. IncompleteRead) is not an OSError, but
            # it means the same thing here: the service did not answer.
            raise CloudUnavailable(str(e)) from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise CloudError("bad_response",
                             "the Eggie service sent something that is not JSON",
                             status) from None

    def device_code(self, client_name: str):
        return self.call("POST", "/v1/auth/device/code",
                         body={"client_name": client_name})

    def device_token(self, device_code: str):
        return self.call("POST", "/v1/auth/device/token",
                         body={"device_code": device_code})

    def refresh(self, refresh_token: str):
        return self.call("POST", "/v1/auth/token/refresh",
                         body={"refresh_token": refresh_token})

    def logout(self, token: str):
        return self.call("POST", "/v1/auth/logout", token=token)

    def me(self, token: str):
        return self.call("GET", "/v1/identity/me", token=token)

    def create_project(self, token: str, name: str, client_ref: str):
        return self.call("POST", "/v1/projects", token=token,
                         body={"name": name, "client_ref": client_ref})

    def delete_project(self, token: str, cloud_id: str):
        return self.call("DELETE",
                         f"/v1/projects/{urllib.parse.quote(cloud_id, safe='')}",
                         token=token)

    def _public_url_path(self, cloud_id: str) -> str:
        return f"/v1/tunnels/projects/{urllib.parse.quote(cloud_id, safe='')}/url"

    def create_public_url(self, token: str, cloud_id: str, routes: list[dict],
                          origin: str):
        return self.call("POST", self._public_url_path(cloud_id), token=token,
                         body={"origin": origin, "routes": routes})

    def active_public_url(self, token: str):
        """The account's one active URL, whichever device made it; 404 if none."""
        return self.call("GET", "/v1/tunnels/url", token=token)

    def release_public_url(self, token: str, cloud_id: str):
        return self.call("DELETE", self._public_url_path(cloud_id), token=token)
