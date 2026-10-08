from eggie_api.infra.github import GitHubError

CODE = {"device_code": "dc-1", "user_code": "WDJB-MJHT",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900, "interval": 5}
TOKEN = "gho_secret123"
USER = {"login": "octo", "id": 42, "name": "Octo Cat", "email": None}


class FakeGitHub:
    """Each method pops its next scripted reply; an Exception is raised, a
    callable is called (to act mid-request) and its result used."""

    def __init__(self, **scripts):
        self.scripts = {name: list(replies) for name, replies in scripts.items()}
        self.calls = []

    def _next(self, name, *args):
        self.calls.append((name, *args))
        reply = self.scripts[name].pop(0)
        if callable(reply) and not isinstance(reply, type):
            reply = reply()
        if isinstance(reply, Exception):
            raise reply
        return reply

    def device_code(self, client_id):
        return self._next("device_code", client_id)

    def device_token(self, client_id, device_code):
        return self._next("device_token", client_id, device_code)

    def user(self, token):
        return self._next("user", token)

    def repos(self, token, page):
        return self._next("repos", token, page)


def err(code, interval=None):
    return GitHubError(code, code, 401 if code == "bad_credentials" else 200, interval)
