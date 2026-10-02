from __future__ import annotations

# CONFIRMED ONCE, then still mostly UNVERIFIED. On 2026-09-15, on macOS
# 26.6.2 (build 25G83) arm64 with Lima 2.2.0, a VM created from this
# provider's `create()` against the pre-33571da revision of omelet.yaml
# booted under `vz`, ran the full engine bootstrap, and answered both
# declared portForwards (39080, 39099) -- confirmed after the fact, not by a
# controlled session anyone recorded as it happened. See
# docs/macos-status.md (it points at the full command trail in git history).
# Still unrun anywhere: `forward()`/`forwards()` and the ssh control master
# they assume, `stop()`/`destroy()`, the uninstall path, whether
# omelet.yaml's `ssh.localPort` is honoured by Lima at all (the one VM
# inspected predates that field), the x86_64 image, and whether Rosetta is
# functional inside the guest -- `rosetta.enabled: true` only proved not to
# block boot.

import os
import platform
import shutil
import subprocess
from pathlib import Path

from . import lima_install
from ..core.provider import Access, AccessField, Completed, Diagnosis, CheckResult, Runtime

LOOPBACK = "127.0.0.1"

# The value omelet.yaml's `ssh.localPort` requests -- not confirmed as what
# Lima actually binds. Not shown to the user for that reason (see
# _PORT_UNCONFIRMED below): the one VM this project has inspected predates
# the field, so whether Lima honours it is still an open question in
# docs/macos-status.md. Kept here only as the value asked for.
DECLARED_SSH_PORT = "39022"

# What the screen shows for Port before Lima has written its own ssh.config
# -- deliberately not DECLARED_SSH_PORT. Showing that number here would read
# as a live value a user could `ssh -p` with, and it may not even be the
# port Lima picks (the inspected VM ended up on a different one).
_PORT_UNCONFIRMED = "assigned when the virtual machine is created"

_SSH_KEYS = {"hostname": "Host", "port": "Port", "user": "User",
             "identityfile": "Identity file"}


def parse_ssh_config(text: str) -> dict[str, str]:
    """The four fields an editor's Remote-SSH dialog asks for.

    Lima writes this file when it creates the VM, and LimaProvider.forward()
    already hands the same path to `ssh -F`. Parsing it here gives that
    assumption a second reader: if Lima ever moves or renames it, the status
    screen says so in plain sight instead of a port forward failing quietly.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        label = _SSH_KEYS.get(parts[0].lower())
        if label and label not in found:
            found[label] = parts[1].strip().strip('"')
    return found


# Host is a fact this project's own code guarantees, by construction --
# portForwards always binds it to LOOPBACK, and that much needs no VM to
# check. Port is different: Omelet requests it via omelet.yaml's
# `ssh.localPort`, but no run of this project has ever confirmed Lima honours
# that field rather than picking its own free port -- the one VM inspected so
# far predates the field entirely (see docs/macos-status.md).
# User and Identity file are never configured anywhere: they are guesses at
# Lima's usual behaviour for a fresh guest account (`getpass.getuser()`,
# `_config/user`), and a host username Lima sanitizes when provisioning the
# guest (spaces, uppercase, unicode) makes the guess wrong. The fallback note
# below must say which kind of default each missing field is, not lump them
# together as "the values Omelet asks Lima for".
_CONFIGURED_LABELS = ("Host",)
_REQUESTED_LABELS = ("Port",)
_GUESSED_LABELS = ("User", "Identity file")
_ALL_LABELS = _CONFIGURED_LABELS + _REQUESTED_LABELS + _GUESSED_LABELS


def _join_and(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _fallback_note(missing: list[str], *, config_exists: bool) -> str:
    """Names exactly which fields are shown from a default instead of from
    Lima's own ssh.config, so the screen never mixes real and guessed values
    with no indication which is which -- a truncated write or a future Lima
    format change can leave some fields present and others missing, not just
    all-or-nothing. A user reading this has not got a shell yet, so it says
    what to do about it in the same two sentences.
    """
    if not missing:
        return ""
    configured = [label for label in _CONFIGURED_LABELS if label in missing]
    requested = [label for label in _REQUESTED_LABELS if label in missing]
    guessed = [label for label in _GUESSED_LABELS if label in missing]
    clauses = []
    if configured:
        verb = "is" if len(configured) == 1 else "are"
        clauses.append(f"{_join_and(configured)} {verb} what Omelet asks Lima for")
    if requested:
        verb = "is" if len(requested) == 1 else "are"
        clauses.append(f"{_join_and(requested)} {verb} requested in omelet.yaml "
                       "but not confirmed until Lima writes its own ssh.config")
    if guessed:
        verb = "is" if len(guessed) == 1 else "are"
        clauses.append(f"{_join_and(guessed)} {verb} Lima's own usual default "
                       "and may not match once the virtual machine exists")
    lead = ("The virtual machine has not been started yet" if not config_exists
            else "Lima has not written all of its connection details yet")
    return f"{lead}, so " + "; ".join(clauses) + ". Run setup, then open this window again."


# Homebrew installs limactl here and puts neither prefix on the PATH an app
# launched from Finder is given -- LaunchServices starts one with
# /usr/bin:/bin:/usr/sbin:/sbin, and a GUI process inherits no shell profile.
# `which limactl` answering yes in a terminal is therefore not the question the
# setup window is asking, which is how it came to tell a user who had just run
# `brew install lima` to install Lima.
BREW_PREFIXES = ("/opt/homebrew/bin", "/usr/local/bin")


def default_data_root() -> Path:
    """Everything the host keeps for itself on this platform: the VM directory,
    the download cache and the managed Lima. The single literal -- the provider
    factory's default_install_dir() is derived from it, so the two cannot
    disagree about where setup put limactl."""
    return Path.home() / ".local" / "share" / "omelet"


def find_limactl(name: str = "limactl", *, which=shutil.which,
                 prefixes=BREW_PREFIXES, managed: Path | None = None) -> str:
    """Absolute path to limactl, or `name` unchanged if it was not found.

    The managed copy wins whenever it exists: setup installs a pinned version,
    and every assumption LimaProvider makes about Lima's on-disk layout is an
    assumption about that version. A user's Homebrew Lima is never removed,
    never upgraded and never used once ours is there -- the fallback below
    exists for one case, a source checkout that has never run setup.
    """
    if os.sep in name:
        return name             # an explicit path: a test, or a bundled copy
    if managed is not None and os.access(managed, os.X_OK):
        return str(managed)
    found = which(name)
    if found:
        return found
    for prefix in prefixes:
        candidate = os.path.join(prefix, name)
        if os.access(candidate, os.X_OK):
            return candidate
    return name


def _default_runner(argv):
    return subprocess.run(argv, capture_output=True)


class LimaProvider:
    def __init__(self, name="omelet-vm", config: Path | None = None,
                 limactl="limactl", runner=_default_runner,
                 lima_home: Path | None = None, data_root: Path | None = None,
                 mac_ver=None, machine=None, login_items=None):
        self.name = name
        self.config = Path(config) if config else None
        self.limactl = limactl
        self._run = runner
        self.lima_home = Path(lima_home) if lima_home else Path.home() / ".lima"
        self.data_root = Path(data_root) if data_root else default_data_root()
        # Injectable for the same reason Wsl2Provider's `facts` is: platform.mac_ver()
        # returns ('', ..., ...) on the Windows host this suite mostly runs on, so a
        # test exercising preflight()/is_supported() needs a real version to check
        # the >= 13 gate against.
        self._mac_ver = mac_ver or platform.mac_ver
        # Injectable for the same reason: platform.machine() on the Windows host
        # returns the Windows architecture, not the target macOS one.
        self._machine = machine or platform.machine
        # Resolved lazily so importing this module never needs ServiceManagement.
        self._login_items = login_items

    def _spawn(self, argv: list[str]) -> Completed:
        p = self._run(argv)
        return Completed(p.returncode,
                         (p.stdout or b"").decode("utf-8", "replace").strip("\n"),
                         (p.stderr or b"").decode("utf-8", "replace").strip("\n"))

    def _cmd(self, args: list[str]) -> Completed:
        return self._spawn([self.limactl, *args])

    @staticmethod
    def _require(result: Completed, what: str) -> Completed:
        """Same contract as the WSL2 provider's: `_cmd()` returns a `Completed`
        and never raises, so a dropped result reports success for a VM that was
        never created. A caller must not be able to tell which platform it is
        on, and that includes how loudly a failure arrives."""
        if result.ok:
            return result
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{what} (exit {result.returncode})"
                           + (f": {detail}" if detail else "."))

    def is_supported(self) -> Diagnosis:
        """What `omelet doctor` prints: the whole truth about this machine.

        Wider than preflight() on purpose, and the reverse of the WSL2
        provider, where preflight is the richer of the two. Setup installs Lima
        itself now, so preflight must not stop for it -- but a user running
        doctor still deserves to be told it is missing, and told that setup is
        what fixes it.
        """
        checks = list(self._os_checks())
        checks.append(self._lima_version_check())
        return Diagnosis(checks)

    def _lima_version_check(self) -> CheckResult:
        """The label claims a version (`f"Lima {LIMA_VERSION}"`), which this
        branch made load-bearing: every on-disk-layout assumption LimaProvider
        makes -- where `runtime()`'s install puts things, what `access()`
        parses -- is a 2.2.0 assumption. Checking only that *some* `limactl`
        is executable told a Mac with Homebrew Lima 1.x, and no managed copy,
        "Lima 2.2.0 ✓". Actually running it and matching the version string is
        the same check `lima_install._require_version` makes on a freshly
        downloaded binary.
        """
        label = f"Lima {lima_install.LIMA_VERSION}"
        from shutil import which
        present = os.access(self.limactl, os.X_OK) or which(self.limactl) is not None
        if not present:
            return CheckResult(label, False,
                               "run Omelet setup, which installs Lima for you")
        result = self._spawn([self.limactl, "--version"])
        output = f"{result.stdout} {result.stderr}"
        matches = result.ok and lima_install.LIMA_VERSION in output
        return CheckResult(
            label, matches,
            None if matches else
            "a different Lima is on this Mac; run Omelet setup, which "
            "installs the version this app was built for")

    def _os_checks(self):
        release = self._mac_ver()[0]
        head = release.split(".")[0]
        # "10.16" is not a real release: it's what a process without a
        # "supports macOS 11" manifest sees under SYSTEM_VERSION_COMPAT,
        # whatever the true OS is (11 or newer) -- so it is treated as
        # unparseable, like the "" platform.mac_ver() returns off macOS. A
        # version we cannot read is our ignorance about this Mac, not
        # evidence it is too old: get_provider() only returns this provider
        # on darwin at all, and vz is the real gate, failing later with its
        # own message if this machine truly cannot run it. Only a version
        # that parses AND reads below 13 is a dead end.
        parsed = int(head) if head.isdigit() and release != "10.16" else None
        ok = parsed is None or parsed >= 13
        # vz, which omelet.yaml asks for, is macOS 13+. The .pkg refuses to
        # install below that; a source checkout has nothing stopping it.
        yield CheckResult(
            f"macOS 13 or newer (found {release or 'unknown'})", ok,
            None if ok else
            "Omelet needs macOS 13 or newer; this Mac cannot run it")

    def exists(self) -> bool:
        out = self._cmd(["list", "--quiet"]).stdout
        return self.name in [line.strip() for line in out.splitlines()]

    def running(self) -> bool:
        out = self._cmd(["list", "--format", "{{.Status}}", self.name])
        return out.ok and out.stdout.strip() == "Running"

    def create(self) -> None:
        if self.config is None:
            raise ValueError("config path is required to create the VM")
        self._require(
            self._cmd(["start", f"--name={self.name}", "--tty=false", str(self.config)]),
            f"the virtual machine '{self.name}' could not be created")

    def start(self) -> None:
        self._require(self._cmd(["start", self.name]),
                      f"the virtual machine '{self.name}' could not be started")

    def stop(self) -> None:
        self._require(self._cmd(["stop", self.name]),
                      f"the virtual machine '{self.name}' could not be stopped")

    def destroy(self) -> None:
        self._require(self._cmd(["delete", self.name]),
                      f"the virtual machine '{self.name}' could not be removed")

    def recover(self, *, everything: bool = False) -> None:
        # Nothing outside this instance to restart, so `everything` is the same
        # forced restart. Nothing on this side raises VmUnresponsive yet.
        self._require(self._cmd(["stop", "--force", self.name]),
                      f"the virtual machine '{self.name}' could not be stopped")
        self.start()

    recover_warning = "This forces the virtual machine to stop first. Nothing is deleted."

    def exec(self, argv: list[str], *, root: bool = False) -> Completed:
        prefix = ["sudo", *argv] if root else list(argv)
        return self._cmd(["shell", self.name, *prefix])

    # --- port forwarding ---
    #
    # Lima keeps an ssh control master for every running VM, and writes the
    # config that reaches it next to the VM's own state. `ssh -O forward` asks
    # that existing connection for one more tunnel, so there is no daemon to
    # supervise and nothing to clean up if the host process goes away. No
    # elevation: these are host ports the user already owns.

    def _ssh_config(self) -> Path:
        return self.lima_home / self.name / "ssh.config"

    def _control(self, verb: str, guest_port: int, host_port: int) -> Completed:
        return self._spawn(["ssh", "-F", str(self._ssh_config()),
                            "-O", verb, "-L", f"{host_port}:{LOOPBACK}:{guest_port}",
                            f"lima-{self.name}"])

    def forward(self, guest_port: int, host_port: int) -> None:
        if guest_port == host_port:
            # Declared in omelet.yaml's portForwards and set up when the VM
            # starts; asking for it again would only fail as a duplicate.
            return
        # `-O forward` fails on a tunnel that already exists, and says so only
        # in prose, so cancelling first makes the pair idempotent by
        # construction rather than by matching an error string.
        self._control("cancel", guest_port, host_port)
        self._require(
            self._control("forward", guest_port, host_port),
            f"port {host_port} could not be forwarded to {guest_port} in the VM")

    def unforward(self, guest_port: int, host_port: int) -> None:
        if guest_port == host_port:
            return
        # Not checked: ssh exits non-zero for a tunnel that is not there, and
        # releasing a forward nobody added is the expected case on cleanup.
        self._control("cancel", guest_port, host_port)

    def forwards(self) -> list[tuple[int, int]]:
        """Always empty, and that is the answer rather than a gap.

        The tunnels live in the control master, which dies with the VM, so a
        restart cannot leak one and there is nothing to release. The WSL2 twin
        has to enumerate because netsh stores its table in the registry.
        """
        return []

    def preflight(self) -> Diagnosis:
        """What setup gates on before it installs anything. Only facts about
        this computer that no step can change."""
        return Diagnosis(list(self._os_checks()))

    def access(self) -> Access:
        """What the status screen shows a user who wants a shell -- or a
        coding agent -- inside the VM. See parse_ssh_config() above for why
        this reads Lima's own ssh.config rather than shelling `limactl
        show-ssh` or hand-assembling the command from `self.name` alone."""
        config = self._ssh_config()
        try:
            found = parse_ssh_config(config.read_text())
            config_exists = True
        except OSError:
            found = {}
            config_exists = False
        missing = [label for label in _ALL_LABELS if label not in found]
        note = _fallback_note(missing, config_exists=config_exists)
        import getpass
        fields = (
            AccessField("Host", found.get("Host", LOOPBACK)),
            AccessField("Port", found.get("Port", _PORT_UNCONFIRMED)),
            AccessField("User", found.get("User", getpass.getuser())),
            AccessField("Identity file", found.get(
                "Identity file", str(self.lima_home / "_config" / "user"))),
        )
        return Access(
            headline="Connect a coding agent",
            summary=("Your coding agent runs inside the virtual machine, where "
                     "Docker and the omelet command already are. Open a shell "
                     "there with the command below, or point an editor's "
                     "Remote-SSH at these values."),
            command=f"ssh -F {config} lima-{self.name}",
            fields=fields,
            note=note)

    def _items(self):
        if self._login_items is None:
            from .mac_login import MainAppLoginItem
            self._login_items = MainAppLoginItem()
        return self._login_items

    def autostart_enabled(self, exe_path: str) -> bool:
        return self._items().enabled()

    def set_autostart(self, on: bool, exe_path: str) -> None:
        """exe_path is unused: SMAppService registers this .app bundle itself."""
        if on:
            self._items().register()
        else:
            self._items().unregister()

    def watch_login_launch(self, on_login) -> None:
        """Must be called before webview.start(): it hooks pywebview's app delegate."""
        from .mac_login import install_login_launch_handler
        install_login_launch_handler(on_login)

    def on_window_shown(self, visible: bool) -> None:
        from .mac_login import set_dock_visible
        set_dock_visible(visible)

    def single_instance(self, on_show, *, announce: bool) -> bool:
        """LaunchServices keeps one instance of a .app; a second open arrives
        as the reopen event, which the tray turns into Open Omelet."""
        return True

    def runtime(self) -> Runtime | None:
        def install(emit) -> None:
            # Rebind: on a clean Mac find_limactl() ran before this download
            # existed and answered the bare name, which is on no PATH an app
            # launched from Finder is given. Without this the very next step
            # shells out to a limactl that is not there, and the failure
            # arrives attributed to create_vm instead of here.
            self.limactl = str(lima_install.install(self.data_root, on_progress=emit))

        return Runtime(f"Installing Lima {lima_install.LIMA_VERSION}", install)

    def apply_remedy(self, remedy: str) -> None:
        raise ValueError(f"unknown remedy: {remedy}")

    def reboot_required(self) -> bool:
        return False   # no OS features to enable; Lima needs no restart

    def reboot(self) -> None:
        """Restart macOS now.

        Through Apple Events rather than `shutdown -r`, which needs root: this
        prompts the user exactly as choosing Restart from the Apple menu does.
        Unreachable in practice -- reboot_required() is always False here --
        but the Protocol is satisfied honestly rather than with a pass.
        """
        self._run(["osascript", "-e", 'tell application "System Events" to restart'])

    # Nothing on macOS to turn on: the Virtualization framework is part of the
    # OS, so setup has no remediation step and no restart to gate on. Both
    # steps are dropped from the list rather than shown and skipped -- a Mac
    # user watching "Turning on Windows features" learns the wrong thing about
    # what this program is doing.
    remediable = False

    def installer_asset(self, version: str) -> str:
        # The .pkg is built native-arch: an arm64 package refuses an Intel Mac.
        return f"OmeletSetup-{version}-{self._machine()}.pkg"

    def launch_installer(self, path: Path) -> None:
        self._run(["open", str(path)])

    def register_resume(self, exe_path: str) -> None:
        """Nothing to resume: `reboot_required()` is always False here, so the
        gate never raises and setup never has to survive a restart."""

    def image(self):
        """No rootfs for the host to fetch.

        `images:` in omelet.yaml names the guest image and `limactl start`
        downloads and caches it, so the host has nothing to download and the
        install list drops its download step. None is the answer, not a URL
        nobody reads -- see `default_steps`.
        """
        return None

    @property
    def location(self) -> Path:
        """Where limactl keeps this VM. Not the host's install dir: Lima owns
        the disk image and its config, and `limactl delete` is what removes
        them."""
        return self.lima_home / self.name

    # Named for the user, in the finish message.
    terminal = "Terminal"
