from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from ..core.images import WSL_IMAGES
from ..core.provider import (Access, AccessField, Completed, Diagnosis, ProviderError,
                             VmUnresponsive)
from . import codex_app, codex_trust, registry
from .wsl_encoding import decode_wsl
from .wsl_checks import diagnose_wsl2, preflight_checks

log = logging.getLogger(__name__)

RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
# Docker's own shutdown-timeout is 15 s; this leaves systemd room for the rest.
POWEROFF_WAIT = 30.0
_RESUME_VALUE_NAME = "EggieSetup"

# The user clicked No on the UAC prompt (ERROR_CANCELLED).
ELEVATION_DECLINED = 1223

# Both ends of a portproxy rule; see the forwarding section below.
LOOPBACK = "127.0.0.1"

# Absent off Windows, where this module is still imported by the test suite.
# 0 is "no extra creation flags", which is what every non-Windows Popen wants.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# WSL terminates a distro seconds after its last attached wsl.exe exits, even
# with systemd and Docker running inside. This session is that attached
# process; its argv[0] is how start() finds one already holding the VM.
HOLD_NAME = "eggie-hold"

# WSAETIMEDOUT from WSL's service: the utility VM behind every distro stopped
# answering. `wsl -l --running` still lists the distro; `wsl --shutdown` clears it.
_HUNG_CODE = "0x8007274c"


def _default_runner(argv):
    return subprocess.run(argv, capture_output=True, creationflags=_NO_WINDOW)


def _default_spawner(argv):
    # Not waited on and outlives the app: the browser UI must keep working
    # after the window closes. `wsl --terminate` in stop() ends it.
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)


def _default_opener(target: str) -> None:
    # ShellExecute, as Start-Process does: the only way to hand Windows an app's own link.
    os.startfile(target)  # type: ignore[attr-defined]


def _default_facts() -> dict:
    from . import default_install_dir  # local import: providers/__init__ imports this module

    def wmi(query: str) -> str:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                             capture_output=True, creationflags=_NO_WINDOW)
        return out.stdout.decode("utf-8", "replace").strip()

    version = subprocess.run(["wsl.exe", "--version"], capture_output=True,
                             creationflags=_NO_WINDOW)
    # `wsl --status`'s exit code alone tells us the OS features are on,
    # without an elevated Get-WindowsOptionalFeature/DISM call: preflight
    # must stay unelevated so apply_remedy's UAC prompt is the only one.
    status = subprocess.run(["wsl.exe", "--status"], capture_output=True,
                            creationflags=_NO_WINDOW)
    return {
        "wsl_version_text": decode_wsl(version.stdout),
        "build": sys.getwindowsversion().build,
        "hypervisor_present":
            wmi("(Get-CimInstance Win32_ComputerSystem).HypervisorPresent") == "True",
        "firmware_virtualization":
            wmi("(Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled") == "True",
        # Measure the drive that will actually hold the VM, not Python's
        # drive. Anchor rather than the full path: on a first run the
        # install directory doesn't exist yet.
        "free_gb": shutil.disk_usage(
            Path(default_install_dir()).anchor or sys.prefix).free / 1024 ** 3,
        "wsl_features_enabled": status.returncode == 0,
    }


def _default_elevator(exe: str, args: list[str]) -> int:
    """Run `exe args` elevated, wait for it, and return its real exit code.

    ShellExecuteW cannot be used here: it reports success as soon as the
    process is *launched*, so a `wsl --install` that Windows Update or group
    policy rejected would be recorded as a success. ShellExecuteExW with
    SEE_MASK_NOCLOSEPROCESS hands back a process handle to wait on, which also
    makes back-to-back remedies serial instead of concurrent.
    """
    import ctypes
    from ctypes import wintypes

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NOASYNC = 0x00000100      # keep the call valid past our own return
    INFINITE = 0xFFFFFFFF
    SW_SHOWNORMAL = 1

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),     # union with hMonitor
            ("hProcess", wintypes.HANDLE),
        ]

    # use_last_error keeps a private copy of the thread error taken right at the
    # call boundary. ctypes.GetLastError() reads the live value, which any
    # intervening Win32 call can clobber — and the value that matters here is
    # ERROR_CANCELLED, the most common failure the installer sees.
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = " ".join(args)
    info.nShow = SW_SHOWNORMAL

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        # A declined UAC prompt lands here as ERROR_CANCELLED (1223).
        return ctypes.get_last_error() or 1
    if not info.hProcess:
        return 1
    try:
        kernel32.WaitForSingleObject(info.hProcess, INFINITE)
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            return ctypes.get_last_error() or 1
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def _default_arch() -> str:
    import platform
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"


_AGENT_CLIS = {"claude-code": "claude", "codex": "codex", "cursor": "cursor"}


def _agent_apps(env, agent_id: str) -> list[Path]:
    """Where each coding agent's Windows app lands, by runtime/agents/ id."""
    local = env.get("LOCALAPPDATA")
    programs = env.get("ProgramFiles")
    found = []
    if local:
        # Store apps' execution aliases, one folder per package.
        aliases = Path(local) / "Microsoft" / "WindowsApps"
        found += {
            "claude-code": [aliases / "Claude_pzs8sxrjxfjjc" / "claude-desktop.exe",
                            # The older installer's app; not seen on a real machine.
                            Path(local) / "AnthropicClaude" / "claude.exe"],
            "codex": [aliases / "OpenAI.Codex_2p2nqsd0c76g0" / "chatgpt-desktop.exe"],
            "cursor": [Path(local) / "Programs" / "cursor" / "Cursor.exe"],
        }.get(agent_id, [])
    if programs and agent_id == "cursor":
        # Cursor's all-users install; not seen on a real machine.
        found.append(Path(programs) / "cursor" / "Cursor.exe")
    return found


class RootfsMissing(FileNotFoundError):
    code = "rootfs_missing"


class Wsl2Provider:
    def __init__(self, distro="eggie-vm", install_dir: Path | None = None,
                 rootfs: Path | None = None, wsl="wsl.exe", runner=_default_runner,
                 facts=_default_facts, elevator=_default_elevator,
                 registry_writer=registry.write_value, arch=None,
                 spawner=_default_spawner, opener=_default_opener,
                 sleep=time.sleep, clock=time.monotonic,
                 env=os.environ, which=shutil.which):
        self.distro = distro
        self.install_dir = Path(install_dir) if install_dir else None
        self.rootfs = Path(rootfs) if rootfs else None
        self.wsl = wsl
        self._run = runner
        self._spawn = spawner
        self._open = opener
        self._facts = facts
        self._elevate = elevator
        self._write_registry = registry_writer
        self._sleep = sleep
        self._clock = clock
        self._arch = arch or _default_arch()
        self._features_enabled = False
        self._env = env
        self._which = which

    # --- wsl.exe's own output is UTF-16LE ---
    def _meta(self, args: list[str]) -> Completed:
        p = self._run([self.wsl, *args])
        return self._answered(
            Completed(p.returncode, decode_wsl(p.stdout), decode_wsl(p.stderr)))

    def _answered(self, result: Completed) -> Completed:
        if result.ok:
            return result
        # exec() decodes as UTF-8, but wsl.exe's own errors may be UTF-16LE.
        detail = (result.stderr or result.stdout).replace("\x00", "").strip()
        if _HUNG_CODE in detail:
            raise VmUnresponsive(
                f"the virtual machine '{self.distro}' is not answering: {detail}")
        return result

    @staticmethod
    def _require(result: Completed, what: str, code: str) -> Completed:
        """`exec()` and `_meta()` return a `Completed` and never raise, so a
        dropped result is a silent success: `eggie vm start` printed "VM
        started." for a distro that does not exist, and an unchecked wsl.conf
        write surfaced minutes later as `systemctl enable --now docker`
        failing for no visible reason."""
        if result.ok:
            return result
        detail = (result.stderr or result.stdout).strip()
        raise ProviderError(code, f"{what} (exit {result.returncode})"
                            + (f": {detail}" if detail else "."))

    def is_supported(self) -> Diagnosis:
        return diagnose_wsl2(self._run, self.wsl)

    def exists(self) -> bool:
        return self.distro in self._distros()

    def _distros(self) -> list[str]:
        out = self._meta(["-l", "-q"]).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]

    def running(self) -> bool:
        # Not exec(): any `wsl -d` boots the distro. With nothing running this
        # exits non-zero and prints a sentence rather than an empty list.
        out = self._meta(["-l", "--running", "-q"])
        return out.ok and self.distro in [line.strip() for line in out.stdout.splitlines()]

    def create(self) -> None:
        if self.install_dir is None or self.rootfs is None:
            raise ValueError("install_dir and rootfs are required to create the VM")
        if not self.rootfs.exists():
            raise RootfsMissing(f"rootfs not found: {self.rootfs}")
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self._require(
            self._meta(["--import", self.distro, str(self.install_dir),
                        str(self.rootfs), "--version", "2"]),
            f"the virtual machine '{self.distro}' could not be created",
            "vm_import_failed")
        # systemd is off by default in WSL; docker.service needs it. Checked:
        # a failed write here means systemd stays off, and the only symptom is
        # bootstrap dying minutes later at `systemctl enable --now docker`.
        # Replacing the whole file is safe only because the import above just
        # succeeded: it refuses an existing distro, so nothing the runtime
        # wrote (default user, no drives) can be here yet.
        self._require(
            self.exec(["bash", "-lc",
                       "printf '[boot]\\nsystemd=true\\n' > /etc/wsl.conf"],
                      root=True),
            f"systemd could not be turned on inside '{self.distro}'",
            "vm_configure_failed")
        # Not stop(): systemd is not running yet, so its poweroff path would
        # only wait out the poll. --terminate makes wsl.conf apply on next boot.
        self._require(self._meta(["--terminate", self.distro]),
                      f"the virtual machine '{self.distro}' could not be restarted",
                      "vm_start_failed")
        self.start()  # like Lima's create: the VM is left running

    def start(self) -> None:
        # Running any command boots the distro, but only keeps it up while it
        # runs -- the hold is what keeps it running afterwards.
        self._require(self.exec(["true"]),
                      f"the virtual machine '{self.distro}' could not be started",
                      "vm_start_failed")
        if not self.exec(["pgrep", "-f", f"^{HOLD_NAME}"], root=True).ok:
            self._spawn([self.wsl, "-d", self.distro, "-u", "root", "--cd", "~", "--",
                         "bash", "-c", f"exec -a {HOLD_NAME} sleep infinity"])

    def stop(self) -> None:
        # --terminate alone pulls the plug on Docker and every project
        # database. Powering off through systemd stops them first; whether
        # poweroff also ends the distro depends on the WSL version, so
        # --terminate follows either way.
        # Any `wsl -d` boots a stopped distro, and a hung VM answers nothing:
        # neither case gets a poweroff or a wait.
        try:
            powered = self.running()
            if powered:
                self.exec(["systemctl", "poweroff"], root=True)
        except VmUnresponsive:
            powered = False
        if powered:
            deadline = self._clock() + POWEROFF_WAIT
            try:
                while self.running() and self._clock() < deadline:
                    self._sleep(1)
            except VmUnresponsive:
                pass
        self._require(self._meta(["--terminate", self.distro]),
                      f"the virtual machine '{self.distro}' could not be stopped",
                      "vm_stop_failed")

    def destroy(self) -> None:
        self._require(self._meta(["--unregister", self.distro]),
                      f"the virtual machine '{self.distro}' could not be removed",
                      "vm_remove_failed")

    def exec(self, argv: list[str], *, root: bool = False) -> Completed:
        base = [self.wsl, "-d", self.distro]
        if root:
            base += ["-u", "root"]
        # Without --cd, WSL maps this process's Windows folder into the VM,
        # which has no drive mounts to map it to: it warns, then falls back.
        base += ["--cd", "~", "--", *argv]
        p = self._run(base)
        # command passthrough is UTF-8
        return self._answered(
            Completed(p.returncode,
                      p.stdout.decode("utf-8", "replace").strip("\n"),
                      p.stderr.decode("utf-8", "replace").strip("\n")))

    def recover(self, *, everything: bool = False) -> None:
        # --terminate touches only this distro; a hang in the utility VM that
        # every distro shares clears only with --shutdown.
        if everything:
            self._require(self._meta(["--shutdown"]), "WSL could not be restarted",
                          "wsl_restart_failed")
        else:
            self.stop()
        self.start()

    recover_warning = ("Restarting everything also stops every other Linux "
                       "distribution and Docker Desktop on this computer. "
                       "Nothing is deleted.")

    # --- port forwarding ---
    #
    # `netsh interface portproxy` writes to HKLM and needs administrator
    # rights, so every call here goes through the same elevator the installer
    # uses for `wsl --install`. There is deliberately no second UAC pathway.
    #
    # Both sides of the proxy are 127.0.0.1: localhostForwarding already puts
    # a guest port listening on 0.0.0.0 onto host loopback at the same number,
    # so the proxy only has to move it to a different number. Pointing it at
    # the VM's own address instead would leave a rule behind that stops working
    # the next time the VM boots with a different one.

    @staticmethod
    def _delete_rule(host_port: int) -> str:
        return ("netsh interface portproxy delete v4tov4 "
                f"listenaddress={LOOPBACK} listenport={host_port}")

    @staticmethod
    def _add_rule(guest_port: int, host_port: int) -> str:
        return ("netsh interface portproxy add v4tov4 "
                f"listenaddress={LOOPBACK} listenport={host_port} "
                f"connectaddress={LOOPBACK} connectport={guest_port}")

    def _netsh(self, *rules: str) -> int:
        """Run the rules in one elevated shell and return the last one's code.

        One invocation, not one per rule: each trip through the elevator is its
        own UAC prompt, and `cmd /c a & b` exits with b's code -- which is what
        makes a delete of a rule that is not there free rather than fatal.
        """
        return self._elevate("cmd.exe", ["/c", " & ".join(rules)])

    def forward(self, guest_port: int, host_port: int) -> None:
        if guest_port == host_port:
            # localhostForwarding already covers this; a proxy on top would add
            # a hop, and a UAC prompt, for nothing.
            return
        # `add` refuses an address/port it already listens on, and says so in
        # the console's own language, so the error cannot be matched. Deleting
        # first makes the pair idempotent by construction.
        code = self._netsh(self._delete_rule(host_port),
                           self._add_rule(guest_port, host_port))
        if code == ELEVATION_DECLINED:
            raise ProviderError("elevation_declined",
                f"forwarding port {host_port} to {guest_port} in the VM needs "
                "administrator approval (the permission prompt was dismissed).")
        if code != 0:
            raise ProviderError("port_forward_failed",
                f"port {host_port} could not be forwarded to {guest_port} in "
                f"the VM (code {code}). Another program may already be "
                f"listening on {host_port}.")

    def unforward(self, guest_port: int, host_port: int) -> None:
        if guest_port == host_port:
            return
        # Not checked: netsh exits non-zero for a rule that is not there, and
        # releasing a forward nobody added is the expected case on cleanup.
        self._netsh(self._delete_rule(host_port))

    def forwards(self) -> list[tuple[int, int]]:
        """Every (guest_port, host_port) pair Windows is currently proxying.

        netsh keeps this table in the registry, so it survives a reboot and is
        the only record of what was allocated -- and the only thing `unforward`
        can be driven from. `show` needs no elevation. The header rows are
        localized; the four-column shape of a data row is not, so the parse
        keys on that instead of on any word.
        """
        p = self._run(["netsh", "interface", "portproxy", "show", "v4tov4"])
        out = []
        for line in p.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) != 4 or not (parts[1].isdigit() and parts[3].isdigit()):
                continue
            out.append((int(parts[3]), int(parts[1])))
        return out

    def preflight(self) -> Diagnosis:
        return preflight_checks(**self._facts())

    def access(self) -> Access:
        """No SSH, and no pretending otherwise. A WSL distro runs no sshd; the
        way in is wsl.exe, and the way to the files is the UNC path Explorer
        and every Windows editor already understand."""
        from ..core import constants
        return Access(
            headline="Connect a coding agent",
            summary=("Your coding agent runs inside the virtual machine, where "
                     "Docker and the eggie command already are. Open a shell "
                     "there with the command below."),
            command=f"wsl -d {self.distro} --cd ~",
            fields=(
                AccessField("Virtual machine", self.distro),
                AccessField("Projects folder",
                            rf"\\wsl$\{self.distro}"
                            + constants.GUEST_PROJECTS.replace("/", "\\")),
            ))

    def apply_remedy(self, remedy: str) -> None:
        if remedy not in ("enable_wsl_features", "update_wsl"):
            raise ValueError(f"unknown remedy: {remedy}")
        args = (["--install", "--no-distribution"] if remedy == "enable_wsl_features"
                else ["--update"])
        code = self._elevate(self.wsl, args)
        if code == ELEVATION_DECLINED:
            raise ProviderError("elevation_declined",
                f"`wsl {' '.join(args)}` needs administrator approval "
                "(the permission prompt was dismissed). Re-run setup and "
                "choose Yes when Windows asks.")
        if code != 0:
            raise ProviderError(
                "wsl_features_failed" if remedy == "enable_wsl_features"
                else "wsl_update_failed",
                f"`wsl {' '.join(args)}` could not be completed (code {code}). "
                "Windows Update may be busy, or company policy may block WSL. "
                "Restart the computer and run setup again.")
        if remedy == "enable_wsl_features":
            self._features_enabled = True

    def reboot_required(self) -> bool:
        return self._features_enabled

    def reboot(self) -> None:
        """Restart Windows now.

        /t 0 rather than a delay: the user pressed a button that says
        "Restart now", and a countdown they cannot see is worse than none.
        Resume is already registered by the gate before this is reachable.
        """
        self._run(["shutdown", "/r", "/t", "0"])

    def installer_asset(self, version: str) -> str:
        return f"EggieSetup-{version}.exe"

    def launch_installer(self, path: Path) -> None:
        # The installer closes this app itself and relaunches it when done
        # (installer.iss); the app must not wait for it.
        self._spawn([str(path), "/SILENT", "/SUPPRESSMSGBOXES",
                     "/CLOSEAPPLICATIONS", "/NORESTART"])

    def register_resume(self, exe_path: str) -> None:
        self._write_registry(RUNONCE_KEY, _RESUME_VALUE_NAME,
                             f'"{exe_path}" setup --resume')

    def image(self):
        return WSL_IMAGES[self._arch]

    def ssh_shortcut(self):
        """None: the distro runs no sshd, so there is no host to name."""
        return None

    # The agents reach the distro through WSL itself. Codex on Windows runs
    # only in the default distro, so connecting it makes this one the default
    # for every WSL app: the app asks first, and can put the old one back.
    agent_connections = ("codex",)
    # No sshd in the distro: the agents reach it through WSL.
    local_ssh_alias = None

    def connect_agent(self, agent_id: str) -> str:
        if agent_id not in self.agent_connections:
            raise ValueError(f"Eggie can't connect '{agent_id}' on this computer")
        self._require(self._meta(["--set-default", self.distro]),
                      f"'{self.distro}' could not be made the default WSL distro",
                      "vm_configure_failed")
        try:
            codex_trust.trust(codex_app.default_codex_home(self._env, Path.home()),
                              self._codex_folders())
        except (OSError, ValueError) as e:
            # Only spares the person Codex's Trust folder question.
            log.warning("could not mark the projects folder trusted in Codex: %s", e)
        return f"{self.distro} is now your default WSL distro."

    def _codex_folders(self) -> list[str]:
        # The keys Codex itself writes: its lowercased Windows path, and the
        # Linux path when Codex runs in WSL.
        return [rf"\\?\unc\wsl.localhost\{self.distro}\opt\eggie\projects".lower(),
                "/opt/eggie/projects"]

    def agent_connected(self, agent_id: str) -> bool:
        """Whether this distro is the default, so the guide can skip the button."""
        return agent_id == "codex" and self._default_distro() == self.distro

    def switches_default(self, agent_id: str) -> str | None:
        """The distro connecting `agent_id` would make every WSL app's default;
        None when it is already, or when connecting changes nothing outside Eggie."""
        if agent_id != "codex" or self._default_distro() == self.distro:
            return None
        return self.distro

    def current_default(self) -> str | None:
        return self._default_distro()

    def can_restore_default(self, name: str) -> bool:
        """Whether `name` is another installed distro and this one is still the default."""
        return (bool(name) and name != self.distro
                and self._default_distro() == self.distro and name in self._distros())

    def restore_default(self, name: str) -> str:
        if not self.can_restore_default(name):
            raise ValueError(f"'{name}' can't be made the default WSL distro")
        self._require(self._meta(["--set-default", name]),
                      f"'{name}' could not be made the default WSL distro",
                      "vm_configure_failed")
        return f"{name} is your default WSL distro again."

    def agent_app(self, agent_id: str) -> str | None:
        """The agent's app, to open it from the app; None for a CLI-only install."""
        # exists(), not is_file(): a Store alias is a reparse point.
        return next((str(p) for p in _agent_apps(self._env, agent_id) if p.exists()), None)

    def agent_installed(self, agent_id: str) -> bool:
        cli = _AGENT_CLIS.get(agent_id)
        return cli is not None and (self.agent_app(agent_id) is not None
                                    or self._which(cli) is not None)

    def open_agent(self, agent_id: str, local: bool = False) -> bool:
        """`local`: open it on this computer's kitchen, which only Codex can be told."""
        app = self.agent_app(agent_id)
        if app is None:
            return False
        if local and agent_id == "codex":
            folder = rf"\\wsl.localhost\{self.distro}\opt\eggie\projects"
            try:
                self._open("codex://threads/new?path=" + quote(folder, safe=""))
                return True
            except OSError as e:
                # A Codex build without the codex:// handler still opens, without the folder.
                log.warning("opening Codex on the projects folder failed: %s", e)
        self._spawn([app])
        return True

    # Which of the app's agent guides (`ui/agents/*/agent.json` steps) apply here.
    guide_os = "windows"

    def computer_name(self) -> str:
        return socket.gethostname()

    def _default_distro(self) -> str | None:
        # `-l -v` marks the default with `*`. Plain `-l` says "(Default)",
        # which Windows translates. Neither boots a distro.
        out = self._meta(["-l", "-v"])
        if not out.ok:
            return None
        for line in out.stdout.replace("\x00", "").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == "*":
                return fields[1]
        return None

    def runtime(self):
        """Nothing to install: wsl.exe ships with Windows, and what it needs
        turned on is `remediable` above, not a download."""
        return None

    @property
    def location(self) -> Path:
        """Where `wsl --import` put the distro's vhdx."""
        return self.install_dir

    # Named for the user, in the finish message.
    terminal = "PowerShell"

    # WSL2 and VirtualMachinePlatform are Windows features setup can turn on,
    # and turning them on needs a restart.
    remediable = True

    # wsl.conf is read only when the distro starts.
    restart_after_first_bootstrap = True
