from __future__ import annotations

# macOS only, and here rather than in host/core/ for the reason every other
# platform fact is: this module knows an architecture name, a release URL and
# a tarball layout, and none of that may leak into the installer.

import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from ..core.download import fetch as _fetch
from ..core.images import Image

LIMA_VERSION = "2.2.0"

_RELEASE = ("https://github.com/lima-vm/lima/releases/download/"
            f"v{LIMA_VERSION}/lima-{LIMA_VERSION}-Darwin-%s.tar.gz")

# Digests from the release's SHA256SUMS. `fetch` refuses anything else, so a
# wrong value here is a failed install, never a silently substituted binary.
#
# These are the main tarballs, which carry the guest agent for the host's own
# architecture. lima-additional-guestagents-* exists for running a guest of a
# different architecture and is deliberately not fetched: eggie.yaml asks for
# a native-arch Ubuntu, and the extra download is 38 MB nobody would use.
ARCHIVES: dict[str, Image] = {
    "arm64": Image(_RELEASE % "arm64",
                   "bbdef91774885a0d05f7b048c4eb89ae2bcf3a0c252ae7ca7934e63df76d93c3"),
    "x86_64": Image(_RELEASE % "x86_64",
                    "0d6f99c19f6e4bc3c92730c4c29d929e6927f0cb0a0ba1a84383367135a8ff31"),
}

_MACHINES = {"arm64": "arm64", "aarch64": "arm64",
             "x86_64": "x86_64", "amd64": "x86_64"}


class LimaInstallError(RuntimeError):
    """Lima could not be put on disk, or what landed there does not run."""


def _default_runner(argv):
    return subprocess.run(argv, capture_output=True)


def archive_for(machine: str) -> Image:
    key = _MACHINES.get(machine.lower())
    if key is None:
        raise LimaInstallError(
            f"there is no Lima build for this Mac's processor ({machine})")
    return ARCHIVES[key]


def managed_root(root: Path) -> Path:
    return Path(root) / "lima"


def managed_limactl(root: Path) -> Path:
    # bin/ and share/ must stay siblings: limactl resolves share/lima
    # relative to its own executable, so a flattened copy finds no templates
    # and no guest agent.
    return managed_root(root) / "bin" / "limactl"


def _machine() -> str:
    import platform
    return platform.machine()


def install(root: Path, *, fetch=_fetch, runner=_default_runner,
            on_progress=None, machine: str | None = None) -> Path:
    """Put the pinned Lima under `root/lima` and return the path to limactl.

    Idempotent: a matching `.version` and an executable binary is the whole
    check, so a re-run costs one file read and no network.

    Downloading rather than bundling is not only a size decision. build.sh
    signs the app with `codesign --deep`, which re-signs every Mach-O in the
    bundle and would strip the com.apple.security.virtualization entitlement
    Lima ad-hoc signs limactl with -- breaking vz on signed builds only, on
    the user's machine. Bytes unpacked from the release tarball keep Lima's
    own signature, and urllib attaches no com.apple.quarantine xattr, so
    neither Gatekeeper nor our signing is in this path at all.
    """
    root = Path(root)
    target = managed_root(root)
    binary = managed_limactl(root)
    version_file = target / ".version"

    if binary.is_file() and os.access(binary, os.X_OK):
        try:
            if version_file.read_text().strip() == LIMA_VERSION:
                return binary
        except OSError:
            pass

    archive = fetch(archive_for(machine or _machine()),
                    root / "cache" / f"lima-{LIMA_VERSION}.tar.gz",
                    on_progress=on_progress)

    staging = Path(tempfile.mkdtemp(prefix="lima-", dir=str(root)))
    try:
        _extract(Path(archive), staging)
        staged_binary = staging / "bin" / "limactl"
        if not staged_binary.is_file():
            raise LimaInstallError(
                "the Lima download did not contain bin/limactl")
        staged_binary.chmod(0o755)
        (staging / ".version").write_text(LIMA_VERSION + "\n")
        # Verified here, in staging, and not after the swap: a process killed
        # mid-check between "moved into place" and "confirmed to run" would
        # leave the real path holding an unverified binary -- a swap the user
        # cannot see coming and this function cannot undo once it has
        # happened. Verifying first means a failed check has moved nothing,
        # so there is nothing to roll back.
        _require_version(staged_binary, runner)
        _swap(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return binary


def _extract(archive: Path, into: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            # `filter="data"` below rejects a `..` escape and a link out of the
            # tree, but silently *normalizes* an absolute name rather than
            # refusing it -- the same finding as eggie_api/infra/files.py. Refusing
            # it here is the check that is actually missing.
            if member.name.startswith("/") or Path(member.name).is_absolute():
                raise LimaInstallError(
                    f"the Lima download contains an absolute path: {member.name}")
        tar.extractall(into, filter="data")


def _require_version(binary: Path, runner) -> None:
    """Run it. An unpacked tarball is not a working binary, and the difference
    is invisible until create_vm fails minutes later with something unrelated."""
    result = runner([str(binary), "--version"])
    output = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")
    if result.returncode != 0:
        raise LimaInstallError(
            f"the downloaded limactl did not run (exit {result.returncode})"
            + (f": {output.strip()}" if output.strip() else "."))
    if LIMA_VERSION not in output:
        raise LimaInstallError(
            f"the downloaded limactl reports {output.strip()!r}, "
            f"not version {LIMA_VERSION}")


def _swap(staging: Path, target: Path) -> None:
    """Move the verified tree into place, keeping the old one until the last
    moment: a half-installed Lima is worse than the previous version.

    The tree moved in here has already passed `_require_version` in staging,
    so there is nothing left to roll back if this fails partway -- the
    `previous` side-step exists only so a failed `os.replace(staging, target)`
    (a cross-device move, say) can put the old tree back rather than leave
    neither.
    """
    previous = target.with_name(target.name + ".previous")
    shutil.rmtree(previous, ignore_errors=True)
    if target.exists():
        os.replace(target, previous)
    try:
        os.replace(staging, target)
    except OSError:
        if previous.exists():
            os.replace(previous, target)
        raise
    shutil.rmtree(previous, ignore_errors=True)
