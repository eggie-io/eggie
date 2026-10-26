class FakeInstance:
    """Replays scripted answers per method, in order, like FakeCloud: an
    exception is raised, a callable is called with the call's arguments.
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
        if callable(reply):
            return reply(*args)
        return reply

    def names(self):
        return [call[0] for call in self.calls]

    def open(self, host, *, ticket, size, data, replace):
        return self._next("open", host, ticket, size, data, replace)

    def upload(self, host, push_id, token, path, on_progress):
        reply = self._next("upload", host, push_id, token, path)
        on_progress(path.stat().st_size)
        return reply

    def status(self, host, push_id, token):
        return self._next("status", host, push_id, token)
