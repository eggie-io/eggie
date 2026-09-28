class FakeCloud:
    """Replays scripted answers per method, in order; an exception is raised.
    Records every call as (method, *args)."""

    def __init__(self, **replies):
        self.replies = {name: list(values) for name, values in replies.items()}
        self.calls = []

    def _next(self, name, *args):
        self.calls.append((name, *args))
        queue = self.replies.get(name)
        assert queue, f"unexpected call to {name}{args}"
        reply = queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def names(self):
        return [call[0] for call in self.calls]

    def device_code(self, client_name):
        return self._next("device_code", client_name)

    def device_token(self, device_code):
        return self._next("device_token", device_code)

    def refresh(self, refresh_token):
        return self._next("refresh", refresh_token)

    def logout(self, token):
        return self._next("logout", token)

    def me(self, token):
        return self._next("me", token)

    def create_project(self, token, name, client_ref):
        return self._next("create_project", token, name, client_ref)

    def delete_project(self, token, cloud_id):
        return self._next("delete_project", token, cloud_id)

    def create_public_url(self, token, cloud_id, routes, origin):
        return self._next("create_public_url", token, cloud_id, routes, origin)

    def active_public_url(self, token):
        return self._next("active_public_url", token)

    def release_public_url(self, token, cloud_id):
        return self._next("release_public_url", token, cloud_id)


CODE = {"device_code": "dc-1", "user_code": "ABCD-EFGH",
        "verification_uri": "https://svc/device",
        "verification_uri_complete": "https://svc/device?user_code=ABCD-EFGH",
        "expires_in": 600, "interval": 5}
TOKENS = {"access_token": "at-1", "refresh_token": "rt-1",
          "token_type": "Bearer", "expires_in": 900}
ME = {"user": {"id": "u", "email": "ada@example.com", "created_at": "x"},
      "current_org_id": "org-1", "organizations": []}
