import queue

from eggie_api.infra.actions_helper import HelperError, target_key


class FakeTerminal:
    """Echoes what is written. `read` blocks like the real one until there is
    output, `hang_up()` or `close()`."""

    def __init__(self):
        self.written = []
        self.resizes = []
        self.closed = False
        self.write_error = None
        self._out: queue.Queue[bytes] = queue.Queue()

    def read(self):
        return self._out.get()

    def write(self, data):
        if self.write_error is not None:
            raise self.write_error
        self.written.append(data)
        self._out.put(data)

    def resize(self, cols, rows):
        self.resizes.append((cols, rows))

    def hang_up(self):
        self._out.put(b"")

    def close(self):
        self.closed = True
        self._out.put(b"")


class FakeHelper:
    """In-memory eggie-actions: one run per target key, every call recorded.
    Set `fail` to an exception and every call raises it. Keys are `target_key`s:
    ("project", p, a) or ("connection", c)."""

    def __init__(self):
        self.calls = []
        self.runs: dict[tuple, dict] = {}
        self.fail = None
        self.terminals: list[FakeTerminal] = []
        self.outputs: dict[tuple, str] = {}
        self.screens: dict[tuple, dict] = {}
        self.typed: list[tuple[tuple, str]] = []
        self.checks: dict[str, int | None] = {}

    def _record(self, *call):
        self.calls.append(call)
        if self.fail is not None:
            raise self.fail

    def start(self, target, command, *, log, deadline=None):
        key = target_key(target)
        self._record("start", key, command, log, deadline)
        run = self.runs.get(key)
        if run and run["state"] == "running":
            raise HelperError("action_running", "this action is already running")
        self.runs[key] = run = {"state": "running", "exit_code": None,
                                "started_at": 1700000000}
        return run

    def stop(self, target):
        key = target_key(target)
        self._record("stop", key)
        run = self.runs.get(key)
        if run:
            run.update(state="exited", exit_code=143)
        return run

    def run(self, target):
        key = target_key(target)
        self._record("run", key)
        return self.runs.get(key)

    def screen(self, target):
        key = target_key(target)
        self._record("screen", key)
        if key not in self.runs:
            raise HelperError("not_running", "this action has no run to show")
        return self.screens.get(key, {"rows": 24, "cursor_y": 0, "history_size": 0,
                                      "text": ""})

    def type(self, target, text):
        key = target_key(target)
        self._record("type", key, text)
        if key not in self.runs:
            raise HelperError("not_running", "this action has no run to type into")
        self.typed.append((key, text))

    def check(self, command):
        self._record("check", command)
        return self.checks.get(command, 0)

    def status(self, project):
        self._record("status", project)
        return {k[2]: run for k, run in self.runs.items()
                if k[:2] == ("project", project)}

    def stop_project(self, project):
        self._record("stop_project", project)
        for key in [k for k in self.runs if k[:2] == ("project", project)]:
            del self.runs[key]

    def attach(self, target, cols, rows):
        key = target_key(target)
        self._record("attach", key, cols, rows)
        if key not in self.runs:
            raise HelperError("not_running", "this action has no run to attach to")
        self.terminals.append(FakeTerminal())
        return self.terminals[-1]

    def output(self, target):
        key = target_key(target)
        self._record("output", key)
        if key not in self.runs:
            raise HelperError("not_running", "this action has no run to show")
        return self.outputs.get(key, "")

    def names(self):
        return [c[0] for c in self.calls]
