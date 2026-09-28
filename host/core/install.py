from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from host.core.constants import API_UNCONFIGURED

# Resolved from this module rather than from cli.py: cli.py is the frozen
# entry script, whose __file__ points at the bundle root instead of at
# host/, so an entry-script lookup misses the bundled template.
VERIFY_TEMPLATE = (Path(__file__).resolve().parent.parent
                    / "provision" / "nginx-hello")


class InstallState:
    """Which steps have finished, so a resume or re-run skips them."""

    def __init__(self, path):
        self._path = Path(path)

    def completed(self) -> set[str]:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return set()
        steps = data.get("completed", [])
        return set(steps) if isinstance(steps, list) else set()

    def mark(self, step: str) -> None:
        done = self.completed()
        done.add(step)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"completed": sorted(done)}))

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


def remove_vm_data(root: Path, install_dir: Path) -> None:
    """Everything that goes with the VM itself.

    install_dir is removed even though `destroy()` normally empties it: a
    failed or partial destroy strands a multi-gigabyte disk image there.
    """
    InstallState(root / "install-state.json").clear()
    shutil.rmtree(install_dir, ignore_errors=True)


def remove_downloads(root: Path) -> None:
    """The cached guest image and the managed Lima runtime.

    Separate from remove_vm_data because the desktop app offers this as its
    own choice -- it is disk space, not state, and a user may want to keep it
    to avoid re-downloading several hundred megabytes.
    """
    shutil.rmtree(root / "cache", ignore_errors=True)
    # macOS only in practice (root/lima is never created on Windows), but
    # harmless to remove unconditionally: setup's install_runtime step puts
    # the managed Lima here (lima_install.managed_root), and leaving it
    # behind was the ~100 MB --purge never actually cleaned up.
    shutil.rmtree(root / "lima", ignore_errors=True)


@dataclass(frozen=True)
class Progress:
    step: str
    status: str          # running | done | skipped | failed | reboot
    message: str = ""
    # How far through a long step we are, 0.0-1.0, and only for a step that
    # declared `progress=True`. None on every other event, including the
    # `running` one that opens such a step.
    fraction: float | None = None


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[..., str | None]
    # Run on every invocation, never recorded as done. Two kinds of step need
    # this: the ones that prove or report the outcome, and the ones whose
    # product lives in the VM -- which can be destroyed without setup ever
    # hearing about it, leaving the state file claiming a distro that is gone.
    always_run: bool = False
    # Plain-language next move for the user when this step fails.
    action: str = ""
    # Set by a provider that names its own step -- "Installing Lima" is Lima's
    # word, not the installer's. Empty means the UI picks the text.
    label: str = ""
    # When true, `run` is called with an `emit(done, total)` callable instead
    # of with nothing. Only the two download steps set it; every other step,
    # and every toy step in the tests, keeps the no-argument signature.
    progress: bool = False


# Shown when RunOnce relaunches setup at logon: a window that opens by itself
# after a restart has to say why it is there.
RESUME_NOTICE = "Continuing setup after the restart — you don't need to do anything."


class RebootRequired(Exception):
    """The machine must restart before the remaining steps can run."""


class InstallError(RuntimeError):
    def __init__(self, step: str, message: str, action: str = ""):
        super().__init__(f"{step}: {message}")
        self.step = step
        self.message = message
        self.action = action


def run_install(steps: list[Step], state: InstallState,
                report: Callable[[Progress], None]) -> None:
    done = state.completed()
    for step in steps:
        if step.name in done and not step.always_run:
            report(Progress(step.name, "skipped"))
            continue
        report(Progress(step.name, "running"))
        try:
            message = step.run(_emitter(step.name, report)) if step.progress else step.run()
        except RebootRequired:
            # The reboot itself satisfies the gate; resuming must step past it.
            state.mark(step.name)
            report(Progress(step.name, "reboot"))
            raise
        except DeadEnd as e:
            report(Progress(step.name, "failed", str(e)))
            raise
        except Exception as e:
            report(Progress(step.name, "failed", str(e)))
            raise InstallError(step.name, str(e), step.action) from e
        # An always-run step is never persisted: recording it would claim a
        # proof that is only valid for the run that produced it.
        if not step.always_run:
            state.mark(step.name)
        report(Progress(step.name, "done", message or ""))


def _emitter(name: str, report: Callable[[Progress], None]):
    """A fraction channel for one step, throttled to whole percents.

    `fetch` calls back once per megabyte, which is 391 events for the rootfs.
    The UI drains a queue on a timer and would cope, but a headless run would
    not, and neither would a log file.
    """
    last = [-1]

    def emit(done: int, total: int) -> None:
        if not total:
            return          # no Content-Length: nothing truthful to report
        percent = int(done * 100 / total)
        if percent == last[0]:
            return
        last[0] = percent
        report(Progress(name, "running", fraction=done / total))

    return emit


class DeadEnd(RuntimeError):
    """A blocking check no code can fix — the user must act."""


def preflight_step(provider) -> None:
    diagnosis = provider.preflight()
    dead = diagnosis.dead_ends
    if dead:
        raise DeadEnd("\n".join(
            f"{c.label}: {c.fix}" if c.fix else c.label for c in dead))


def remediate_step(provider) -> None:
    for check in provider.preflight().fixable:
        provider.apply_remedy(check.remedy)


def reboot_gate_step(provider) -> None:
    if provider.reboot_required():
        raise RebootRequired()


class ApiNotAccepted(RuntimeError):
    """The API is serving, but refuses every authenticated call and a
    re-provision did not change that."""


# The API answers one of these when it has no usable token of its own, or
# when the one this host is holding is not the one it started with. Neither
# clears with time: the token is read once, at container startup.
_TOKEN_CODES = frozenset({API_UNCONFIGURED, "unauthorized"})


class ApiIncompatible(RuntimeError):
    """The API serves an API number this host does not speak."""


# `docker compose up -d` returns before the API container is serving, so the
# first call after an install or a repair legitimately answers "connection
# refused".
API_RESTART_TIMEOUT = 30.0


def _once_serving(call, sleep, timeout: float):
    from host.client import ApiUnavailableError
    deadline = time.monotonic() + timeout
    while True:
        try:
            return call()
        except ApiUnavailableError:
            if time.monotonic() >= deadline:
                raise
        sleep(1.0)


def _incompatible(api) -> str:
    from host.core import constants

    supported = ", ".join(str(n) for n in sorted(constants.SUPPORTED_API))
    return ("This app and the Omelet service inside the virtual machine are "
            "versions that cannot work together.\n"
            f"service API {api}, app supports {supported}")


def connect_step(provider, *, client=None, reconnect=None, update=None,
                 sleep=time.sleep):
    """Check the API speaks a version this host supports and accepts this
    host's token.

    `update` moves the runtime to a release speaking a supported API and
    returns a client for it. `reconnect` reinstalls the runtime in repair mode
    -- recreating the API so it re-reads its token -- and returns a client
    holding the token the VM has now. Returning None keeps the current client.
    """
    from host.client import ApiClient, ApiError
    from host.core import constants
    from host.core.bootstrap import BootstrapError

    client = client or ApiClient.for_provider(provider)
    # 0.1.0 APIs predate the field and serve api 1.
    api = _once_serving(client.health, sleep, API_RESTART_TIMEOUT).get("api", 1)
    updated = False
    if api not in constants.SUPPORTED_API:
        if update is None:
            raise ApiIncompatible(_incompatible(api))
        try:
            client = update() or client
        except BootstrapError as e:
            raise ApiIncompatible(
                f"{_incompatible(api)}\nUpdating it did not work:\n{e}") from e
        api = _once_serving(client.health, sleep, API_RESTART_TIMEOUT).get("api", 1)
        if api not in constants.SUPPORTED_API:
            raise ApiIncompatible(_incompatible(api))
        updated = True
    # /health skips the token check; /version is the cheapest route that does not.
    try:
        client.version()
    except ApiError as e:
        if e.code not in _TOKEN_CODES or reconnect is None:
            raise
        client = reconnect() or client
        try:
            _once_serving(client.version, sleep, API_RESTART_TIMEOUT)
        except ApiError as again:
            if again.code not in _TOKEN_CODES:
                raise
            raise ApiNotAccepted(
                "The Omelet service inside the virtual machine did not accept "
                "this computer, and setting the virtual machine up again did "
                f"not change that.\n{again.message}") from again
        return ("The Omelet service in the virtual machine was not accepting "
                "this computer, and has been reconnected.")
    if updated:
        return "The Omelet service in the virtual machine was updated."
    return None


class VerificationFailed(RuntimeError):
    """The smoke-test project did not serve a successful response."""


def _default_http_get(url: str) -> int:
    from urllib.request import urlopen
    with urlopen(url, timeout=30) as response:
        return response.status


# Traefik publishes a router a beat after the container starts, so the first
# request after `compose up` answers 404 on a stack that is perfectly healthy.
READY_TIMEOUT = 30.0


def _await_http_ok(url: str, http_get, timeout: float, sleep) -> None:
    deadline = time.monotonic() + timeout
    while True:
        error = None
        code = None
        try:
            code = http_get(url)
        except Exception as e:
            error = e
        if code == 200:
            return
        if time.monotonic() >= deadline:
            # First line for the user, second for whoever they send it to.
            if error is not None:
                raise VerificationFailed(
                    f"The test project did not answer at {url}.\n"
                    f"{type(error).__name__}: {error}") from error
            raise VerificationFailed(
                f"The test project answered with an error at {url}.\n"
                f"The address returned HTTP {code} instead of 200.")
        sleep(0.5)


def verify_step(provider, template_dir: Path, domain: str, *, client=None,
                http_get=_default_http_get, ready_timeout: float = READY_TIMEOUT,
                sleep=time.sleep) -> None:
    """Run the bundled template through the API and require HTTP 200.

    The gate is the response the user's browser would get, not the job's own
    verdict: a container can run happily while its URL answers a proxy error.
    """
    from host.client import ApiClient, JobFailedError
    from host.core.constants import VERIFY_PROJECT_ID

    client = client or ApiClient.for_provider(provider)
    try:
        client.ensure_project(VERIFY_PROJECT_ID, domain=domain)
        client.upload_directory(VERIFY_PROJECT_ID, template_dir)
        try:
            result = client.wait_for_job(
                client.project_up(VERIFY_PROJECT_ID)).get("result") or {}
        except JobFailedError as e:
            status = e.result.get("status", "failed")
            raise VerificationFailed(
                "The test project's containers did not stay running.\n"
                f"status: {status}" + (f"\n{e}" if str(e) else "")) from e
        problem = result.get("problem")
        if problem:
            # The containers started, but the API already probed the URL
            # through Traefik and knows why it will not answer. Waiting out
            # the readiness window to report "did not respond" would replace
            # that explanation with a symptom.
            raise VerificationFailed(problem["message"])
        urls = result.get("urls") or []
        if not urls:
            raise VerificationFailed(
                "the smoke-test project exposed no HTTP address")
        _await_http_ok(urls[0], http_get, ready_timeout, sleep)
    finally:
        _teardown(client)


def _teardown(client) -> None:
    """Remove the smoke-test project, containers and state row alike, so a
    failed verify leaves nothing running and `omelet status` stays clean."""
    import sys

    from host.core.constants import VERIFY_PROJECT_ID
    # Read before the call: inside the `except` below, the exception being
    # handled is this one, not the one that is propagating.
    already_failing = sys.exc_info()[0] is not None
    try:
        client.delete_project(VERIFY_PROJECT_ID)
    except Exception as e:
        # Swallowed only while a failure is already on its way out -- masking
        # the reason verification failed is worse than a leaked container. On
        # the success path the leak is the only thing left to report.
        if not already_failing:
            raise VerificationFailed(
                "The test project worked, but it could not be removed from "
                "the virtual machine afterwards. Its containers may still be "
                f"running.\n{e}") from e


def finish_step(location, terminal: str) -> str:
    """The only step whose product is words. Every run must reach it.

    Both values come from the provider: the VM does not live in the same place
    on both platforms (Lima owns its own directory), and neither does the
    terminal the user is being sent to.
    """
    return (
        "Setup finished successfully.\n"
        f"The virtual machine and its files are in: {location}\n\n"
        f"To start a project, open {terminal} and run:\n\n"
        "    omelet up <folder>\n\n"
        "where <folder> is the folder that holds your docker-compose.yml.")


_ACTIONS = {
    "preflight": "This computer could not be checked. Restart it and run setup again.",
    "remediate": "Windows features could not be turned on. Run setup again and "
                 "choose Yes when Windows asks for permission.",
    "reboot_gate": "Restart the computer and run setup again.",
    "fetch_image": "The Linux image could not be downloaded — the internet "
                   "connection was unavailable. Run setup again and the download "
                   "continues from where it stopped.",
    # A checksum mismatch unlinks the partial file (download.fetch), and an
    # unsupported processor never starts a download at all (archive_for) --
    # so "continues from where it stopped" was only ever true for one of the
    # three causes this step can fail with.
    "install_runtime": "Lima could not be downloaded. A dropped internet "
                       "connection resumes on the next run; a checksum "
                       "mismatch starts the download over; an unsupported "
                       "processor cannot run Lima at all. Run setup again.",
    "create_vm": "The virtual machine could not be created or started. Restart "
                 "the computer, make sure there is at least 10 GB free, and "
                 "run setup again.",
    # Neither sentence below may say where the detail is: the headless CLI
    # prints this action after the detail, the wizard shows this action AS
    # the detail label with the raw error in the log box beneath it -- "above"
    # is true in one front door and false in the other.
    "bootstrap": "Omelet could not be installed inside the virtual machine. "
                 "The error came from inside it; open the log for the exact "
                 "text. Run setup again; if it fails the same way twice, "
                 "send us that text.",
    "connect": "The Omelet service inside the virtual machine would not work "
               "with this app, or would not accept this computer. Open the "
               "log to see which. Run setup again; if it fails the same way "
               "twice, use Copy diagnostics and send us the text.",
    "verify": "The test project did not answer. Run setup again; if it fails a "
              "second time, use Copy diagnostics and send us the text.",
}


def default_steps(provider, *, cache_dir, template_dir: Path, domain,
                  exe_path: str) -> list[Step]:
    from .download import fetch

    # None when the VM platform fetches its own guest image -- Lima names it in
    # omelet.yaml and limactl caches it. There is then no rootfs for the host to
    # download and no download step to run, rather than a step that quietly does
    # nothing.
    image = provider.image()
    rootfs = None
    if image is not None:
        # Assigned here rather than inside fetch_image: that step is skipped on
        # a resume or a re-run, and create_vm needs the path in every process.
        rootfs = Path(cache_dir) / image.url.rsplit("/", 1)[-1]
        provider.rootfs = rootfs

    def fetch_image(emit):
        fetch(image, rootfs, on_progress=emit)

    def gate():
        if provider.reboot_required():
            provider.register_resume(exe_path)
        reboot_gate_step(provider)

    def step(name: str, run, *, always_run: bool = False, label: str = "",
             progress: bool = False) -> Step:
        return Step(name, run, always_run=always_run, action=_ACTIONS.get(name, ""),
                    label=label, progress=progress)

    steps = [step("preflight", lambda: preflight_step(provider))]
    # What the VM platform itself needs, which on macOS is Lima. None on
    # Windows, where wsl.exe is part of the OS -- the same shape as image():
    # the step is absent rather than present and skipped.
    runtime = provider.runtime()
    if runtime is not None:
        steps.append(step("install_runtime", runtime.run, always_run=True,
                          label=runtime.label, progress=True))
    # Only where the host OS has features setup can turn on. macOS ships its
    # virtualization framework, so there is nothing to enable and no restart to
    # wait for, and a step list that showed both would be describing Windows.
    if provider.remediable:
        steps += [
            step("remediate", lambda: remediate_step(provider)),
            step("reboot_gate", gate),
        ]
    # Only the steps above may be remembered across runs: they are facts about
    # this computer. Everything below is a fact about the VM, and each
    # re-derives it cheaply -- fetch() returns on a matching digest, create() on
    # an existing distro, bootstrap() on a matching guest marker -- so re-running
    # them costs seconds and skipping them costs a WSL_E_DISTRO_NOT_FOUND
    # minutes later.
    if image is not None:
        steps.append(step("fetch_image", fetch_image, always_run=True, progress=True))
    steps += [
        step("create_vm", lambda: _ensure_vm_running(provider), always_run=True),
        step("bootstrap", lambda: _bootstrap(provider), always_run=True),
        # Before verify: a mismatched or refusing API is one sentence here,
        # not a 404 or 401 minutes into the smoke test.
        step("connect", lambda: connect_with_updates(provider), always_run=True),
        step("verify", lambda: verify_step(provider, template_dir, domain),
             always_run=True),
        step("finish", lambda: finish_step(provider.location, provider.terminal),
             always_run=True),
    ]
    return steps


def _ensure_vm_running(provider) -> None:
    """Existing is not running. `omelet setup`'s summary told a user with a
    stopped VM to "run setup again to start it", but nothing in this list ever
    called `start()` -- Lima's `shell` refuses a stopped instance outright
    ("... is stopped, run 'limactl start ...'"), which is why only a Lima run
    ever surfaced this: `wsl.exe -d` auto-starts a distro, so WSL2 hid the gap
    behind its own exec() calls."""
    if provider.exists():
        provider.start()
    else:
        provider.create()


def _bootstrap(provider, *, repair: bool = False) -> None:
    from .bootstrap import bootstrap
    bootstrap(provider, repair=repair)


def reconnect_runtime(provider):
    """Reinstall in repair mode, which recreates the API container so it
    re-reads its token, then dial it with the token the VM holds now (the
    client caches the one it was built with)."""
    from host.client import ApiClient

    _bootstrap(provider, repair=True)
    return ApiClient.for_provider(provider)


def update_runtime(provider):
    """Move the VM to the newest release speaking an API this host supports."""
    from host.client import ApiClient
    from .bootstrap import bootstrap

    bootstrap(provider, update=True)
    return ApiClient.for_provider(provider)


def connect_with_updates(provider):
    from .runtime_update import declare_supported

    declare_supported(provider)
    return connect_step(provider, reconnect=lambda: reconnect_runtime(provider),
                        update=lambda: update_runtime(provider))
