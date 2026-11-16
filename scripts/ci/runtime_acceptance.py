"""Installs this checkout's runtime on a CI machine the way the host installs
it into a new VM, then checks it the way setup does and a bit further.

    python scripts/ci/runtime_acceptance.py --images <tag>

Run as a user with passwordless sudo on a throwaway Ubuntu 24.04 machine (the
GitHub runner): it installs Docker units, systemd units and accounts for real.

The install goes through the host's own `bootstrap()`, so the command, its
environment and its stub are the ones a user's VM gets. What stands in for the
VM is `RootShell`, which runs each command the way `Wsl2Provider.exec` does:
as root, from /root. The deviations from a real VM are only these, all set
on the root commands:

- EGGIE_RUNTIME_URL / EGGIE_RUNTIME_REPO point at this checkout, packed under
  `archive/` the way GitHub serves it, so a PR installs its own runtime/.
- EGGIE_IMAGE_VERSION / EGGIE_SKIP_PULL use eggie-api and eggie-web built from
  this checkout (packaging/images/build.sh --tag <tag>), not ghcr's.
- No VM restart after the first install (WSL needs one for wsl.conf; nothing
  checked here depends on it).
"""
from __future__ import annotations

import argparse
import os
import pwd
import shlex
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from host.client import ApiClient  # noqa: E402
from host.core import constants  # noqa: E402
from host.core.bootstrap import bootstrap  # noqa: E402
from host.core.install import (VERIFY_TEMPLATE, connect_step,  # noqa: E402
                               reconnect_runtime, verify_step)
from host.core.provider import Completed  # noqa: E402
from host.core.runtime_update import declare_supported  # noqa: E402

REF = "ci"
LIB = ROOT / "runtime" / "install" / "lib"
# What install.sh enables; each must be active once it has run.
UNITS = ["docker.service", "eggie-github.path", "eggie-update-now.path",
         "eggie-update-check.timer", "eggie-agents.path", "eggie-claude-reaper.timer"]
ENABLED_ONLY = ["eggie-update.service"]


class Failed(Exception):
    pass


class RootShell:
    """The VM, as far as the host's setup code can tell: `exec(root=True)` is
    `wsl -d eggie-vm -u root --cd ~ -- <argv>`, which starts in /root."""

    def __init__(self, env: dict[str, str]):
        self._env = [f"{k}={v}" for k, v in env.items()]

    def exec(self, argv: list[str], *, root: bool = False) -> Completed:
        if not root:
            raise Failed(f"unexpected non-root exec: {argv}")
        p = subprocess.run(
            ["sudo", "env", "--chdir=/root", "HOME=/root", *self._env, *argv],
            stdin=subprocess.DEVNULL, capture_output=True, text=True)
        # The installer's own output is the useful part of the CI log.
        if p.stdout:
            print(p.stdout, end="" if p.stdout.endswith("\n") else "\n")
        if p.stderr:
            print(p.stderr, end="" if p.stderr.endswith("\n") else "\n", file=sys.stderr)
        return Completed(p.returncode, p.stdout.strip("\n"), p.stderr.strip("\n"))


def sh(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise Failed(f"{shlex.join(argv)} exited {p.returncode}:\n{p.stdout}{p.stderr}")
    return p


def as_account(name: str, command: str) -> subprocess.CompletedProcess:
    """A login shell as `name` in its home, the way an agent's session starts."""
    return sh(["sudo", "-u", name, "-i", "bash", "-c", command], check=False)


def section(title: str):
    print(f"::endgroup::\n::group::{title}", flush=True)


def check(ok: bool, what: str, detail: str = "") -> None:
    if not ok:
        raise Failed(what + (f"\n{detail}" if detail else ""))
    print(f"ok  {what}", flush=True)


def pack_checkout(dest: Path) -> tuple[str, str]:
    """This checkout as a repo get.sh can read: archive/<ref>.tar.gz with one
    top directory, like GitHub's. Returns (EGGIE_RUNTIME_URL, EGGIE_RUNTIME_REPO)."""
    (dest / "archive").mkdir(parents=True)
    tree = sh(["git", "-C", str(ROOT), "stash", "create"]).stdout.strip() or "HEAD"
    sh(["git", "-C", str(ROOT), "archive", "--format=tar.gz", f"--prefix=eggie-{REF}/",
        "-o", str(dest / "archive" / f"{REF}.tar.gz"), tree])
    dest.chmod(0o755)
    return (f"file://{ROOT}/runtime/install/get.sh", f"file://{dest}")


def login_accounts() -> list[tuple[str, str]]:
    """The accounts install.sh provisions: root plus login-users.sh's pick."""
    listed = sh(["bash", "-c", f"getent passwd | bash {LIB}/login-users.sh /etc/shells"]).stdout
    accounts = [("root", "/root")]
    for line in listed.splitlines():
        name, _uid, _gid, home = line.split(":", 3)
        accounts.append((name, home))
    return accounts


def check_stack(client: ApiClient) -> None:
    marker = sh(["cat", constants.RUNTIME_MARKER]).stdout.strip()
    check(marker == REF, f"{constants.RUNTIME_MARKER} names {REF}", f"found {marker!r}")
    api = client.health().get("api")
    check(api in constants.SUPPORTED_API, f"/health speaks api {api}, which this host supports")
    for unit in UNITS:
        state = sh(["systemctl", "is-active", unit], check=False).stdout.strip()
        check(state == "active", f"{unit} is active", f"state: {state}")
    for unit in ENABLED_ONLY:
        state = sh(["systemctl", "is-enabled", unit], check=False).stdout.strip()
        check(state == "enabled", f"{unit} is enabled", f"state: {state}")
    mode = sh(["sudo", "stat", "-c", "%a %G", constants.GUEST_TOKEN]).stdout.strip()
    check(mode == "640 docker", f"{constants.GUEST_TOKEN} is 640, group docker", f"found {mode}")


def check_accounts() -> None:
    accounts = login_accounts()
    names = [name for name, _ in accounts]
    check("eggie" in names, "the eggie account is among the provisioned ones", f"got {names}")
    for name, home in accounts:
        targets = sh(["python3", str(LIB / "agents.py"), "--agents-dir",
                      str(ROOT / "runtime" / "agents"), "instructions", "--home", home]).stdout
        for target in filter(None, targets.splitlines()):
            body = sh(["sudo", "cat", target], check=False).stdout
            check("<!-- eggie:begin -->" in body, f"{name}: Eggie's instructions in {target}")
        skills = sh(["sudo", "find", "-L", f"{home}/.claude/skills", "-name", "SKILL.md"],
                    check=False).stdout.split()
        check(bool(skills), f"{name}: skills installed under {home}/.claude/skills")
        if name == "root":
            continue
        link = sh(["sudo", "readlink", f"{home}/projects"], check=False).stdout.strip()
        check(link == "/opt/eggie/projects", f"{name}: ~/projects links to /opt/eggie/projects",
              f"found {link!r}")
        # What an agent does first in a session: reach the API and Docker.
        for command in ("eggie status", "docker ps", "sudo -n true", "touch ~/projects/.ci-probe"):
            p = as_account(name, command)
            check(p.returncode == 0, f"{name}: `{command}` works", p.stdout + p.stderr)
        sh(["sudo", "rm", "-f", "/opt/eggie/projects/.ci-probe"])


def setup_and_verify(provider: RootShell) -> ApiClient:
    """What setup runs once the VM exists: bootstrap, connect, verify."""
    declare_supported(provider)
    # No `update`: a runtime whose API this host does not speak fails here
    # instead of being swapped for the newest release.
    connect_step(provider, reconnect=lambda: reconnect_runtime(provider))
    verify_step(provider, VERIFY_TEMPLATE, constants.DEFAULT_DOMAIN)
    print("ok  the test project answered 200 through Traefik", flush=True)
    return ApiClient.for_provider(provider)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--images", required=True,
                        help="tag of locally built eggie-api/eggie-web images")
    args = parser.parse_args()

    print("::group::Prepare", flush=True)
    url, repo = pack_checkout(Path(tempfile.mkdtemp(prefix="eggie-repo-")))
    os.environ["EGGIE_RUNTIME_URL"] = url
    os.environ["EGGIE_RUNTIME_REF"] = REF
    provider = RootShell({"EGGIE_RUNTIME_REPO": repo, "EGGIE_IMAGE_VERSION": args.images,
                          "EGGIE_SKIP_PULL": "1"})
    # On WSL, step 9b creates this account before step 10 provisions it. This
    # machine is no WSL, so make it the same way, with the same script.
    sh(["sudo", "bash", str(LIB / "wsl-user.sh"), "/dev/null", "/etc/sudoers.d"])
    pwd.getpwnam("eggie")

    try:
        section("First install (bootstrap → connect → verify)")
        started = time.monotonic()
        bootstrap(provider)
        print(f"installed in {time.monotonic() - started:.0f}s", flush=True)
        client = setup_and_verify(provider)

        section("The installed machine")
        check_stack(client)
        token = sh(["sudo", "cat", constants.GUEST_TOKEN]).stdout.strip()

        section("Accounts")
        check_accounts()

        section("Repair: install.sh again over a working machine")
        reconnect_runtime(provider)
        client = setup_and_verify(provider)
        check_stack(client)
        again = sh(["sudo", "cat", constants.GUEST_TOKEN]).stdout.strip()
        check(again == token, "the API token survives a reinstall")
        check_accounts()
    except Exception as e:  # noqa: BLE001 -- every failure ends the run the same way
        if not isinstance(e, Failed):
            traceback.print_exc()
        print("::endgroup::", flush=True)
        print(f"::error::{type(e).__name__}: {e}", flush=True)
        return 1
    print("::endgroup::", flush=True)
    print("runtime acceptance passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
