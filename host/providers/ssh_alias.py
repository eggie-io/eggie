from __future__ import annotations

# The `omelet` host in the user's ~/.ssh/config, and the VM's real host keys in
# ~/.ssh/known_hosts, so `ssh omelet` and an editor's Remote-SSH reach the VM
# by name and keep reaching it.
#
# Lima gives cloud-init a fresh instance-id on every `limactl start`, and
# cloud-init's default regenerates the host keys on a new instance. The
# runtime turns that off (install.sh, `ssh_deletekeys: false`), but a reinstall
# is a new VM with new keys either way, and a stale known_hosts entry is then
# a hard "REMOTE HOST IDENTIFICATION HAS CHANGED". Turning host-key checking
# off in the Host block is not enough: Cursor's Remote-SSH runs its own
# client, which reads known_hosts and ignores StrictHostKeyChecking. So the
# entries for the VM's address are replaced with the keys the guest actually
# has, read over Lima's own authenticated channel.
#
# The block is written into ~/.ssh/config itself, between markers, rather than
# pulled in with Include: Cursor's host list does not follow Include. It goes
# just above the first Host/Match line -- see _first_host().

from pathlib import Path

ALIAS = "omelet"
_BEGIN = "# >>> Omelet: the 'omelet' host, rewritten by Omelet setup >>>"
_END = "# <<< Omelet <<<"

# Labels as parse_ssh_config() returns them, to the ssh_config keyword.
_FIELDS = (("Host", "HostName"), ("Port", "Port"), ("User", "User"),
           ("Identity file", "IdentityFile"))


def render(found: dict[str, str]) -> str:
    missing = [label for label, _ in _FIELDS if label not in found]
    if missing:
        raise ValueError("Lima has not written " + ", ".join(missing))
    lines = [_BEGIN, f"Host {ALIAS}"]
    for label, keyword in _FIELDS:
        value = found[label]
        lines.append(f'  {keyword} "{value}"' if " " in value else f"  {keyword} {value}")
    lines += ["  IdentitiesOnly yes", _END]
    return "\n".join(lines) + "\n"


def _split(text: str) -> tuple[list[str], int | None]:
    """The lines with Omelet's block taken out, and where it was.

    Only a complete block is ours: a BEGIN whose END the user deleted is left
    alone, rather than taking everything after it with it."""
    lines = text.splitlines(keepends=True)
    marks = [line.rstrip("\r\n") for line in lines]
    if _BEGIN not in marks:
        return lines, None
    begin = marks.index(_BEGIN)
    if _END not in marks[begin:]:
        return lines, None
    end = marks.index(_END, begin) + 1
    # The blank line install() put after the block.
    if end < len(lines) and not marks[end].strip():
        end += 1
    return lines[:begin] + lines[end:], begin


def _first_host(lines: list[str]) -> int:
    """Where a new block has to go: before the first Host or Match line.

    Not at the top -- every global option and Include after it would then
    apply to `omelet` alone. Not at the end either --
    ssh takes the first value it finds, so a `Host *` with a User line above
    it would override ours."""
    for i, line in enumerate(lines):
        word = line.split(None, 1)[0].lower() if line.strip() else ""
        if word in ("host", "match"):
            return i
    return len(lines)


def _write(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Replace the file in one step, so a crash never leaves it half written.
    Keeps the existing file's mode; a new file gets `mode`."""
    import os
    import tempfile
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def installed(user_config: Path) -> bool:
    try:
        return _split(user_config.read_text())[1] is not None
    except OSError:
        return False


def install(user_config: Path, found: dict[str, str]) -> None:
    block = render(found)
    # Resolved so a dotfiles symlink is written through, not replaced.
    target = user_config.resolve()
    try:
        current = target.read_text()
    except FileNotFoundError:
        current = ""
    lines, at = _split(current)
    if at is None:
        at = _first_host(lines)
    if lines and at == len(lines) and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines[at:at] = [block] + (["\n"] if at < len(lines) else [])
    _write(target, "".join(lines))


def remove(user_config: Path) -> None:
    target = user_config.resolve()
    try:
        current = target.read_text()
    except FileNotFoundError:
        return
    lines, at = _split(current)
    if at is not None:
        _write(target, "".join(lines))


def known_hosts_name(host: str, port: str) -> str:
    return host if port == "22" else f"[{host}]:{port}"


def host_key_lines(name: str, pubkeys: str) -> list[str]:
    """`<type> <key> [comment]` lines from /etc/ssh/ssh_host_*_key.pub, as
    known_hosts entries for `name`. Anything that is not a key is dropped."""
    lines = []
    for line in pubkeys.splitlines():
        parts = line.split()
        if len(parts) >= 2 and (parts[0].startswith("ssh-") or parts[0].startswith("ecdsa-")):
            lines.append(f"{name} {parts[0]} {parts[1]}")
    return lines


def forget(known_hosts: Path, name: str, run) -> None:
    """Drop every entry for `name`. ssh-keygen, not a text match: it also
    finds entries HashKnownHosts has hashed."""
    if known_hosts.exists():
        run(["ssh-keygen", "-R", name, "-f", str(known_hosts)])


def trust(known_hosts: Path, name: str, pubkeys: str, run) -> None:
    lines = host_key_lines(name, pubkeys)
    if not lines:
        raise ValueError("the virtual machine reported no SSH host keys")
    forget(known_hosts, name, run)
    target = known_hosts.resolve()
    existing = target.read_text() if target.exists() else ""
    sep = "" if not existing or existing.endswith("\n") else "\n"
    _write(target, existing + sep + "\n".join(lines) + "\n", mode=0o644)
