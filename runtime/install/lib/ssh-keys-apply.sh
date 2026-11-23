#!/usr/bin/env bash
# Copies the approved SSH keys the API wrote to where sshd will accept them:
# /opt/eggie is group-writable, so StrictModes refuses a keys file under it.
# Run as root by eggie-ssh-keys.service and once by install.sh; safe to repeat.
#   ssh-keys-apply.sh [keys-dir] [target-dir]
set -uo pipefail

DIR="${1:-/opt/eggie/ssh-keys}"
TARGET="${2:-/etc/ssh/eggie-keys}"
PASSWD="${EGGIE_PASSWD_FILE:-/etc/passwd}"

exec python3 - "$DIR/desired.json" "$TARGET" "$PASSWD" <<'PY'
import json, os, re, sys

desired, target, passwd = sys.argv[1:]
LOGIN = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
# fullmatch, not $: a trailing newline must not smuggle in a second line.
KEY = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}( [^\x00-\x1f]{0,64})?")

try:
    with open(desired) as f:
        data = json.load(f)
    login, keys = data["login"], data["keys"]
    if not isinstance(login, str) or not isinstance(keys, list):
        raise ValueError
except (OSError, ValueError, KeyError, TypeError, AttributeError):
    sys.exit(0)

if not LOGIN.fullmatch(login):
    sys.exit(0)

with open(passwd) as f:
    homes = [line.rstrip("\n").split(":") for line in f]
if not any(len(p) >= 6 and p[0] == login and p[5].startswith("/home/")
           for p in homes):
    sys.exit(0)

lines = [k for k in keys if isinstance(k, str) and KEY.fullmatch(k)]

os.makedirs(target, mode=0o755, exist_ok=True)
tmp = os.path.join(target, f".{login}.tmp")
with open(tmp, "w") as f:
    f.write("".join(k + "\n" for k in lines))
os.chmod(tmp, 0o644)
os.replace(tmp, os.path.join(target, login))

for name in os.listdir(target):
    if name != login:
        os.unlink(os.path.join(target, name))
PY
