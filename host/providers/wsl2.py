from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..core.images import WSL_IMAGES
from ..core.provider import Access, AccessField, Completed, Diagnosis, VmUnresponsive
from .wsl_encoding import decode_wsl
from .wsl_checks import diagnose_wsl2, preflight_checks

RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Task Manager's Startup tab leaves the Run value alone and records its own
# switch here: first byte 0x03 = disabled; missing or anything else = enabled.
STARTUP_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
_STARTUP_DISABLED = 0x03
AUTOSTART_VALUE_NAME = "Eggie"
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


def _default_registry_writer(key: str, name: str, value: str) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)


def _default_registry_reader(key: str, name: str) -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
    except FileNotFoundError:
        return None
    return value if isinstance(value, str) else None


def _default_registry_binary_reader(key: str, name: str) -> bytes | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
    except FileNotFoundError:
        return None
    return bytes(value) if isinstance(value, (bytes, bytearray)) else None


def _default_registry_deleter(key: str, name: str) -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
            winreg.DeleteValue(handle, name)
    except FileNotFoundError:
        pass


def _allow_any_foreground() -> None:
    # Windows only lets the process the user just launched take the
    # foreground; without this the first instance's window opens behind.
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    except Exception:
        pass


def run_value(exe_path: str) -> str:
    return f'"{exe_path}" setup --background'


def _gui_exe(exe_path: str) -> str:
    # eggie.exe is the console build; at login it would flash a window.
    path = Path(exe_path)
    if path.name.lower() == "eggie.exe":
        sibling = path.with_name("setup.exe")
        if sibling.exists():
            return str(sibling)
    return exe_path


def _default_arch() -> str:
    import platform
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"


class Wsl2Provider:
    def __init__(self, distro="eggie-vm", install_dir: Path | None = None,
                 rootfs: Path | None = None, wsl="wsl.exe", runner=_default_runner,
                 facts=_default_facts, elevator=_default_elevator,
                 registry_writer=_default_registry_writer, arch=None,
                 spawner=_default_spawner,
                 registry_reader=_default_registry_reader,
                 registry_deleter=_default_registry_deleter,
                 registry_binary_reader=_default_registry_binary_reader,
                 sleep=time.sleep, clock=time.monotonic):
        self.distro = distro
        self.install_dir = Path(install_dir) if install_dir else None
        self.rootfs = Path(rootfs) if rootfs else None
        self.wsl = wsl
        self._run = runner
        self._spawn = spawner
        self._facts = facts
        self._elevate = elevator
        self._write_registry = registry_writer
        self._read_registry = registry_reader
        self._delete_registry = registry_deleter
        self._read_registry_binary = registry_binary_reader
        self._sleep = sleep
        self._clock = clock
        self._arch = arch or _default_arch()
        self._features_enabled = False

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
    def _require(result: Completed, what: str) -> Completed:
        """`exec()` and `_meta()` return a `Completed` and never raise, so a
        dropped result is a silent success: `eggie vm start` printed "VM
        started." for a distro that does not exist, and an unchecked wsl.conf
        write surfaced minutes later as `systemctl enable --now docker`
        failing for no visible reason."""
        if result.ok:
            return result
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{what} (exit {result.returncode})"
                           + (f": {detail}" if detail else "."))

    def is_supported(self) -> Diagnosis:
        return diagnose_wsl2(self._run, self.wsl)

    def exists(self) -> bool:
        out = self._meta(["-l", "-q"]).stdout
        return self.distro in [line.strip() for line in out.splitlines()]

    def running(self) -> bool:
        # Not exec(): any `wsl -d` boots the distro. With nothing running this
        # exits non-zero and prints a sentence rather than an empty list.
        out = self._meta(["-l", "--running", "-q"])
        return out.ok and self.distro in [line.strip() for line in out.stdout.splitlines()]

    def create(self) -> None:
        if self.install_dir is None or self.rootfs is None:
            raise ValueError("install_dir and rootfs are required to create the VM")
        if not self.rootfs.exists():
            raise FileNotFoundError(f"rootfs not found: {self.rootfs}")
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self._require(
            self._meta(["--import", self.distro, str(self.install_dir),
                        str(self.rootfs), "--version", "2"]),
            f"the virtual machine '{self.distro}' could not be created")
        # systemd is off by default in WSL; docker.service needs it. Checked:
        # a failed write here means systemd stays off, and the only symptom is
        # bootstrap dying minutes later at `systemctl enable --now docker`.
        self._require(
            self.exec(["bash", "-lc",
                       "printf '[boot]\\nsystemd=true\\n' > /etc/wsl.conf"],
                      root=True),
            f"systemd could not be turned on inside '{self.distro}'")
        # Not stop(): systemd is not running yet, so its poweroff path would
        # only wait out the poll. --terminate makes wsl.conf apply on next boot.
        self._require(self._meta(["--terminate", self.distro]),
                      f"the virtual machine '{self.distro}' could not be restarted")
        self.start()  # like Lima's create: the VM is left running

    def start(self) -> None:
        # Running any command boots the distro, but only keeps it up while it
        # runs -- the hold is what keeps it running afterwards.
        self._require(self.exec(["true"]),
                      f"the virtual machine '{self.distro}' could not be started")
        if not self.exec(["pgrep", "-f", f"^{HOLD_NAME}"], root=True).ok:
            self._spawn([self.wsl, "-d", self.distro, "-u", "root", "--",
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
                      f"the virtual machine '{self.distro}' could not be stopped")

    def destroy(self) -> None:
        self._require(self._meta(["--unregister", self.distro]),
                      f"the virtual machine '{self.distro}' could not be removed")

    def exec(self, argv: list[str], *, root: bool = False) -> Completed:
        base = [self.wsl, "-d", self.distro]
        if root:
            base += ["-u", "root"]
        base += ["--", *argv]
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
            self._require(self._meta(["--shutdown"]), "WSL could not be restarted")
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
            raise RuntimeError(
                f"forwarding port {host_port} to {guest_port} in the VM needs "
                "administrator approval (the permission prompt was dismissed).")
        if code != 0:
            raise RuntimeError(
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

    def watch_login_launch(self, on_login) -> None:
        """Nothing to watch: the Run value passes --background itself."""

    def on_window_shown(self, visible: bool) -> None:
        """The taskbar button follows the window on its own."""

    def let_session_end_close(self, on_session_end) -> None:
        from .tray_win import let_session_end_close
        let_session_end_close(on_session_end)

    def tray(self, *, icon, on_open, on_settings, on_quit):
        from .tray_win import WinTray
        return WinTray(icon=icon, on_open=on_open, on_settings=on_settings, on_quit=on_quit)

    def single_instance(self, on_show, *, announce: bool) -> bool:
        from . import instance
        try:
            user = getpass.getuser()
        except Exception:
            user = "user"
        # Pipe names are machine-wide: without the user name, another user's
        # Eggie would answer and show its window instead.
        address = rf"\\.\pipe\eggie-{user}"
        return instance.claim(address, "AF_PIPE", on_show, announce=announce,
                              before_show=_allow_any_foreground)

    def apply_remedy(self, remedy: str) -> None:
        if remedy not in ("enable_wsl_features", "update_wsl"):
            raise ValueError(f"unknown remedy: {remedy}")
        args = (["--install", "--no-distribution"] if remedy == "enable_wsl_features"
                else ["--update"])
        code = self._elevate(self.wsl, args)
        if code == ELEVATION_DECLINED:
            raise RuntimeError(
                f"`wsl {' '.join(args)}` needs administrator approval "
                "(the permission prompt was dismissed). Re-run setup and "
                "choose Yes when Windows asks.")
        if code != 0:
            raise RuntimeError(
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

    def autostart_enabled(self, exe_path: str) -> bool:
        if self._read_registry(RUN_KEY, AUTOSTART_VALUE_NAME) != run_value(_gui_exe(exe_path)):
            return False
        approved = self._read_registry_binary(STARTUP_APPROVED_KEY, AUTOSTART_VALUE_NAME)
        return not (approved and approved[0] == _STARTUP_DISABLED)

    def set_autostart(self, on: bool, exe_path: str) -> None:
        if on:
            self._write_registry(RUN_KEY, AUTOSTART_VALUE_NAME,
                                 run_value(_gui_exe(exe_path)))
            self._delete_registry(STARTUP_APPROVED_KEY, AUTOSTART_VALUE_NAME)
        else:
            self._delete_registry(RUN_KEY, AUTOSTART_VALUE_NAME)

    def image(self):
        return WSL_IMAGES[self._arch]

    def ssh_shortcut(self):
        """None: the distro runs no sshd, so there is no host to name."""
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
