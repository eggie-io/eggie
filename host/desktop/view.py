"""Pure mappings from host/core values to what a screen needs.

No provider and no client -- nothing here ever reaches the VM, the network
or the filesystem. That is what makes this the only module in host/desktop
worth testing, and why api.py stays thin enough to read in one sitting.

No user-facing copy lives here. These functions return state identifiers;
ui/index.html holds the words, so the design board stays the single source
for them.
"""
from __future__ import annotations

from dataclasses import dataclass

from host.core import constants
from host.core.status import Readiness


@dataclass(frozen=True)
class Screen:
    """What Home draws, and the one thing the page does before or instead.

    `action` is "", "start_install", "enter_console" or
    "start_runtime_update"; `name` is the template to draw if the action
    does not take the page elsewhere.
    """
    name: str
    action: str = ""
    message: str = ""


HOME_STATES = ("not_installed", "stopped", "running", "wrong")
# Every name screen_for() can return; each must have a template.
SCREENS = frozenset(
    [f"home:{state}" for state in HOME_STATES]
    + ["first-run", "install:running", "unreachable", "unresponsive",
       "runtime-update:running", "runtime-update:failed"])


def screen_for(route: str, state: str, *, first_run: bool, resumed: bool,
               enter_console: bool, update_tried: bool, update_error: str,
               problem: str) -> Screen:
    """Ordered: the earlier rule wins.

    A resumed launch still has the first-run shape (nothing recorded, no VM)
    and must continue setup, not restart it; the runtime update starts once
    per launch, or an update that "succeeds" without fixing the API would
    loop forever.
    """
    if resumed:
        return Screen("install:running", action="start_install")
    if first_run:
        return Screen("first-run")
    if enter_console:
        return Screen("home:running", action="enter_console")
    if route == "update_runtime":
        if not update_tried:
            return Screen("runtime-update:running", action="start_runtime_update")
        return Screen("runtime-update:failed", message=update_error or problem)
    return Screen(f"home:{state}" if route == "home" else route)


def route_for(readiness: Readiness) -> tuple[str, str]:
    """`(route, state)` for a probe result.

    Ordered to match `status.probe()`'s own early returns, with one
    deliberate exception: `problem` is checked before `vm_exists`. A provider
    that threw in `exists()` reports vm_exists=False, which is shape-identical
    to "no VM yet" -- and routing that to "not installed" would offer a Set up
    button on a machine where setup cannot run.
    """
    if readiness.unresponsive:
        # Ahead of "unreachable": its Repair runs through the very command
        # that is hanging.
        return ("unresponsive", "")
    if readiness.problem:
        # The api service refusing /health is the only failure the unreachable
        # screen describes truthfully: its copy claims we can see the machine
        # humming, which is only established once `true` ran in it and the
        # runtime marker was read.
        if readiness.vm_reachable and readiness.runtime_version:
            return ("unreachable", "")
        return ("home", "wrong")
    if not readiness.vm_exists:
        return ("home", "not_installed")
    if not readiness.vm_reachable:
        return ("home", "stopped")
    if not readiness.runtime_version:
        return ("home", "wrong")
    if readiness.api_version not in constants.SUPPORTED_API:
        return ("update_runtime", "")
    return ("home", "running")


from host.core.install import Step

# Carried over from host/setup_app/wizard.py. install_runtime is absent on
# purpose: its text comes from provider.runtime().label, because the version
# in it is Lima's fact, not the installer's -- a second copy here would go
# stale the first time the pinned version changes.
STEP_LABELS = {
    "preflight": "Checking this computer",
    "remediate": "Turning on Windows features",
    "reboot_gate": "Restart needed",
    "fetch_image": "Downloading Linux image",
    "create_vm": "Preparing the virtual machine",
    "ssh_alias": "Adding the eggie SSH host",
    "bootstrap": "Installing Eggie",
    "connect": "Connecting to the Eggie service",
    "verify": "Testing the setup",
    "finish": "Finishing up",
}


def step_label(step: Step) -> str:
    return step.label or STEP_LABELS.get(step.name, step.name)


def rows_for(steps: list[Step]) -> list[dict]:
    """One row per step the factory actually returned.

    Never a fixed seven: default_steps drops install_runtime on Windows (wsl
    ships with the OS) and drops remediate, reboot_gate and fetch_image on
    macOS (no features to enable, and limactl fetches its own image). The
    "Step N of M" counter is derived from this list for the same reason.
    """
    return [{"name": s.name, "label": step_label(s), "progress": s.progress}
            for s in steps]


from host.core.install import DeadEnd, InstallError, Progress, RebootRequired


def progress_event(progress: Progress) -> dict:
    return {
        "type": "step",
        "step": progress.step,
        "status": progress.status,
        "message": progress.message,
        # None, never 0.0: only a step that declared progress=True has a
        # fraction, and a zero here would draw an empty bar on every other row.
        "fraction": progress.fraction,
    }


def terminal_event(exc: BaseException | None) -> dict:
    """How the run ended, in the three shapes the board draws differently.

    run_install has already reported a `failed` Progress naming the row, so
    nothing here needs the step name -- only the ending, because each offers
    a different pair of buttons.
    """
    if exc is None:
        return {"type": "done"}
    if isinstance(exc, RebootRequired):
        return {"type": "reboot"}
    if isinstance(exc, DeadEnd):
        # No code can fix this one, so the failed screen's "Try this step
        # again" is the wrong offer and the UI hides it for this type.
        return {"type": "dead_end", "message": f"{exc}"}
    if isinstance(exc, InstallError):
        return {"type": "failed", "message": exc.message, "action": exc.action}
    return {"type": "failed", "message": f"{exc}", "action": ""}
