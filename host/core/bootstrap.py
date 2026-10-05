from __future__ import annotations

import base64
import os
import re

from host.core import constants


class BootstrapError(RuntimeError):
    """A command run inside the guest failed; carries the guest's own output."""


# Downloaded in full before it runs: `curl | bash` executes whatever arrived
# before a dropped connection. python3 covers a rootfs that ships without curl.
_STUB = """set -euo pipefail
if command -v curl >/dev/null 2>&1; then
  script="$(curl -fsSL "$1")" || { echo "could not download the Eggie installer from $1" >&2; exit 1; }
else
  script="$(python3 -c 'import sys, urllib.request; sys.stdout.write(urllib.request.urlopen(sys.argv[1], timeout=60).read().decode())' "$1")" \\
    || { echo "could not download the Eggie installer from $1" >&2; exit 1; }
fi
EGGIE_RUNTIME_URL="$1" bash -c "$script"
"""
_STUB_PATH = "/tmp/eggie-bootstrap.sh"

# The command reaches the guest as one `bash -lc` argument through wsl.exe or
# ssh, where a space or quote would be parsed again.
_SHELL_SAFE = re.compile(r"[A-Za-z0-9._:/@+=-]+")


def _shell_safe(value: str, name: str) -> str:
    if not _SHELL_SAFE.fullmatch(value):
        raise BootstrapError(
            f"{name} contains characters the VM command line cannot carry: {value!r}")
    return value


def _run(provider, argv, *, step: str):
    result = provider.exec(argv, root=True)
    if not result.ok:
        detail = (result.stderr or result.stdout).strip()
        raise BootstrapError(
            f"{step} failed inside the VM (exit {result.returncode})"
            + (f":\n{detail}" if detail else "."))
    return result


def _installed(provider) -> bool:
    return provider.exec(["test", "-s", constants.RUNTIME_MARKER], root=True).ok


def bootstrap(provider, *, source: str | None = None, repair: bool = False,
              update: bool = False) -> None:
    """Install the runtime in the VM unless it is already there.

    `repair` reinstalls regardless and tells the installer to keep the ref it
    has and recreate the api container. `update` moves to the newest release
    speaking an API this host supports, leaving an up-to-date VM untouched.
    """
    if not repair and not update and _installed(provider):
        return
    url = _shell_safe(
        source or os.environ.get("EGGIE_RUNTIME_URL") or constants.RUNTIME_URL,
        "EGGIE_RUNTIME_URL")
    apis = ",".join(str(n) for n in sorted(constants.SUPPORTED_API))
    assignments = [f"EGGIE_RUNTIME_API={apis}"]
    ref = os.environ.get("EGGIE_RUNTIME_REF")
    if ref:
        assignments.append(f"EGGIE_RUNTIME_REF={_shell_safe(ref, 'EGGIE_RUNTIME_REF')}")
    if repair:
        assignments.append("EGGIE_RUNTIME_REPAIR=1")
    if update:
        assignments.append("EGGIE_RUNTIME_UPDATE=1")
    encoded = base64.b64encode(_STUB.encode()).decode("ascii")
    command = " ".join([*assignments, "bash", _STUB_PATH, url])
    _run(provider,
         ["bash", "-lc", f"echo {encoded} | base64 -d > {_STUB_PATH} && {command}"],
         step="installing Eggie inside the VM")
    # The installer writes the marker last, so its absence means it stopped
    # early without a non-zero status reaching us.
    if not _installed(provider):
        raise BootstrapError(
            "the Eggie installer reported success but left no "
            f"{constants.RUNTIME_MARKER}")
