"""Sets keys in a WSL wsl.conf, leaving every other line as it was.
Run as root by install.sh, WSL only:
    wsl-conf.py <wsl.conf> <section>.<key>=<value>...
Prints one line when the file changed; silent when it already matched.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
KEY = re.compile(r"^\s*([^#;=\s][^=]*?)\s*=")


def apply(text: str, wanted: list[tuple[str, str, str]]) -> str:
    lines = text.splitlines()
    for section, key, value in wanted:
        lines = _set(lines, section, key, value)
    return "\n".join(lines) + "\n" if lines else ""


def _set(lines: list[str], section: str, key: str, value: str) -> list[str]:
    entry = f"{key}={value}"
    current = None
    first_end = None  # where the first [section] ends, to add a missing key
    found = False
    out: list[str] = []
    for line in lines:
        header = SECTION.match(line)
        if header:
            if current == section.lower() and first_end is None:
                first_end = len(out)
            current = header.group(1).strip().lower()
            out.append(line)
            continue
        match = KEY.match(line)
        # Every copy is rewritten: with the section or key repeated, which one
        # WSL honours is not something to bet the isolation on.
        if current == section.lower() and match and match.group(1).lower() == key.lower():
            out.append(entry)
            found = True
            continue
        out.append(line)
    if current == section.lower() and first_end is None:
        first_end = len(out)
    if found:
        return out
    if first_end is not None:
        # Before the blank lines that separate it from the next section.
        while first_end > 0 and not out[first_end - 1].strip():
            first_end -= 1
        return out[:first_end] + [entry] + out[first_end:]
    if out and out[-1].strip():
        out.append("")
    return out + [f"[{section}]", entry]


def _parse(arg: str) -> tuple[str, str, str]:
    name, sep, value = arg.partition("=")
    section, dot, key = name.partition(".")
    if not (sep and dot and section and key):
        raise SystemExit(f"wsl-conf.py: expected <section>.<key>=<value>, got {arg!r}")
    return section, key, value


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit("usage: wsl-conf.py <wsl.conf> <section>.<key>=<value>...")
    path, wanted = argv[0], [_parse(a) for a in argv[1:]]
    try:
        with open(path, encoding="utf-8") as f:
            before = f.read()
    except FileNotFoundError:
        before = ""
    after = apply(before, wanted)
    if after == before:
        return 0
    # Written beside the target and renamed over it: a half-written wsl.conf
    # can stop the VM from booting.
    fd, staged = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(after)
        os.chmod(staged, 0o644)
        os.replace(staged, path)
    except BaseException:
        os.unlink(staged)
        raise
    print(f"updated {path}; it applies from the VM's next start")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
