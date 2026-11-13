import queue

from eggie_api.infra.actions_helper import HelperError


class FakeTerminal:
    """Echoes what is written. `read` blocks like the real one until there is
    output, `hang_up()` or `close()`."""

    def __init__(self):
        self.written = []
        self.resizes = []
        self.closed = False
        self._out: queue.Queue[bytes] = queue.Queue()

    def read(self):
        return self._out.get()

    def write(self, data):
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
    """In-memory eggie-actions: one run per (project, action), every call recorded.
    Set `fail` to an exception and every call raises it."""

    def __init__(self):
        self.calls = []
        self.runs: dict[tuple[str, str], dict] = {}
        self.fail = None
        self.terminals: list[FakeTerminal] = []

    def _record(self, *call):
        self.calls.append(call)
        if self.fail is not None:
            raise self.fail

    def start(self, project, action, command, *, log):
        self._record("start", project, action, command, log)
        run = self.runs.get((project, action))
        if run and run["state"] == "running":
            raise HelperError("action_running", f"{action} is already running")
        self.runs[(project, action)] = run = {"state": "running", "exit_code": None,
                                              "started_at": 1700000000}
        return run

    def stop(self, project, action):
        self._record("stop", project, action)
        run = self.runs.get((project, action))
        if run:
            run.update(state="exited", exit_code=143)
        return run

    def status(self, project):
        self._record("status", project)
        return {a: run for (p, a), run in self.runs.items() if p == project}

    def stop_project(self, project):
        self._record("stop_project", project)
        for key in [k for k in self.runs if k[0] == project]:
            del self.runs[key]

    def attach(self, project, action, cols, rows):
        self._record("attach", project, action, cols, rows)
        if (project, action) not in self.runs:
            raise HelperError("not_running", "this action has no run to attach to")
        self.terminals.append(FakeTerminal())
        return self.terminals[-1]

    def names(self):
        return [c[0] for c in self.calls]
