from __future__ import annotations

# The `omelet` host in the user's own ~/.ssh/config, so `ssh omelet` and an
# editor's Remote-SSH reach the VM by name.
#
# Lima gives cloud-init a fresh instance-id on every `limactl start`, so the
# guest regenerates its host keys on every boot. Anything that remembers them
# -- ~/.ssh/known_hosts, which a plain `ssh -p 39022 127.0.0.1` writes to --
# then fails with "REMOTE HOST IDENTIFICATION HAS CHANGED" after the next
# restart or reinstall. Lima's own ssh.config turns host-key checking off for
# the same reason; this host does the same, which is safe only because the
# port is bound to loopback.
#
# The block lives in a file Omelet owns and rewrites freely; ~/.ssh/config
# gets one Include line. It goes at the top: ssh applies everything after a
# `Host` line to that host, so an Include appended below someone's last Host
# block would only be read for that host.

from pathlib import Path

ALIAS = "omelet"
_MARK = "# Added by Omelet: the 'omelet' SSH host for its virtual machine."

# Labels as parse_ssh_config() returns them, to the ssh_config keyword.
_FIELDS = (("Host", "HostName"), ("Port", "Port"), ("User", "User"),
           ("Identity file", "IdentityFile"))


def render(found: dict[str, str]) -> str:
    missing = [label for label, _ in _FIELDS if label not in found]
    if missing:
        raise ValueError("Lima has not written " + ", ".join(missing))
    lines = ["# Written by Omelet every time setup runs; edits here are lost.",
             f"Host {ALIAS}"]
    for label, keyword in _FIELDS:
        value = found[label]
        lines.append(f'  {keyword} "{value}"' if " " in value else f"  {keyword} {value}")
    lines += ["  IdentitiesOnly yes",
              "  StrictHostKeyChecking no",
              "  UserKnownHostsFile /dev/null",
              "  LogLevel ERROR"]
    return "\n".join(lines) + "\n"


def _include(alias_file: Path) -> str:
    return f'Include "{alias_file}"'


def installed(alias_file: Path, user_config: Path) -> bool:
    try:
        return (alias_file.is_file()
                and _include(alias_file) in user_config.read_text().splitlines())
    except OSError:
        return False


def install(alias_file: Path, user_config: Path, found: dict[str, str]) -> None:
    alias_file.parent.mkdir(parents=True, exist_ok=True)
    alias_file.write_text(render(found))

    include = _include(alias_file)
    # Resolved so a dotfiles symlink is written through, not replaced.
    target = user_config.resolve()
    try:
        current = target.read_text()
    except FileNotFoundError:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        current = None
    if current is not None and include in current.splitlines():
        return
    target.write_text(f"{_MARK}\n{include}\n" + (f"\n{current}" if current else ""))
    if current is None:
        target.chmod(0o600)


def remove(alias_file: Path, user_config: Path) -> None:
    alias_file.unlink(missing_ok=True)
    target = user_config.resolve()
    try:
        lines = target.read_text().splitlines(keepends=True)
    except FileNotFoundError:
        return
    drop = {_MARK, _include(alias_file)}
    kept = [line for line in lines if line.rstrip("\n") not in drop]
    if kept == lines:
        return
    # The blank line install() put between its two lines and the old content.
    if kept and not kept[0].strip() and len(lines) - len(kept) == 2:
        kept = kept[1:]
    target.write_text("".join(kept))
