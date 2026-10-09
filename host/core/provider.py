from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class VmUnresponsive(RuntimeError):
    """The platform under the VM stopped answering, so every command into it
    fails the same way. Raised by `exec()` too: the guest never ran anything,
    and a Completed would read as that command failing inside the VM."""


@dataclass(frozen=True)
class CheckResult:
    label: str
    ok: bool
    fix: str | None = None
    remedy: str | None = None


@dataclass(frozen=True)
class Diagnosis:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def blocking(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]

    @property
    def dead_ends(self) -> list[CheckResult]:
        return [c for c in self.blocking if c.remedy is None]

    @property
    def fixable(self) -> list[CheckResult]:
        return [c for c in self.blocking if c.remedy is not None]


@dataclass(frozen=True)
class Runtime:
    """A program the VM platform needs that setup installs for the user.

    `None` from `provider.runtime()` means the platform ships it -- wsl.exe is
    part of Windows. A value means one step, named by `label`, calling `run`
    with the installer's fraction emitter.
    """
    label: str
    run: Callable[[Callable[[int, int], None] | None], None]


@dataclass(frozen=True)
class AccessField:
    label: str
    value: str


@dataclass(frozen=True)
class Access:
    """How a person -- or their coding agent -- gets a shell inside the VM.

    Rendered by the status screen, which must not know what SSH is: a WSL
    distro runs no SSH server, and a screen that assumed one would be a
    platform branch in the UI layer.
    """
    headline: str
    summary: str
    command: str
    fields: tuple[AccessField, ...] = ()
    note: str = ""


@runtime_checkable
class DesktopPlatform(Protocol):
    """What the desktop app asks of the OS it runs on: nothing about a VM.

    Kept apart from VmProvider so a tray or a login entry never lands on the
    object whose job is making a Linux machine exist.
    """
    def autostart_enabled(self, exe_path: str) -> bool: ...
    def set_autostart(self, on: bool, exe_path: str) -> None: ...
    # True if this process is the first instance; a second one hands the
    # first a "show" and exits.
    def single_instance(self, on_show: Callable[[], None], *, announce: bool) -> bool: ...
    def watch_login_launch(self, on_login: Callable[[], None]) -> None: ...
    def on_window_shown(self, visible: bool) -> None: ...
    def tray(self, *, icon, on_open, on_settings, on_quit): ...
    def let_session_end_close(self, on_session_end: Callable[[], None]) -> None: ...


@runtime_checkable
class VmProvider(Protocol):
    def is_supported(self) -> Diagnosis: ...
    def exists(self) -> bool: ...
    # Must not start the VM: probe() asks it right after the user pressed Stop.
    def running(self) -> bool: ...
    def create(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def destroy(self) -> None: ...
    def exec(self, argv: list[str], *, root: bool = False) -> Completed: ...
    def forward(self, guest_port: int, host_port: int) -> None: ...
    # Restart a VM that raised VmUnresponsive. `everything` may stop more than
    # this VM (see `recover_warning`), so the user is asked before it is used.
    def recover(self, *, everything: bool = False) -> None: ...
    def preflight(self) -> Diagnosis: ...
    def apply_remedy(self, remedy: str) -> None: ...
    def reboot_required(self) -> bool: ...
    def reboot(self) -> None: ...
