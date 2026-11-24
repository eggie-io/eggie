"""This computer's own key for Eggie instances, kept apart from the user's ~/.ssh."""

from __future__ import annotations

from pathlib import Path

from .ssh import spawn


def ensure_key(directory: Path, comment: str, run) -> tuple[Path, str]:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    private = directory / "eggie_ed25519"
    if not private.exists():
        result = spawn(run, ["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                             "-C", comment, "-f", str(private)])
        if not result.ok:
            raise RuntimeError(result.stderr or result.stdout
                               or f"ssh-keygen exited {result.returncode}")
    public = Path(str(private) + ".pub").read_text(encoding="utf-8").strip()
    return private, public
