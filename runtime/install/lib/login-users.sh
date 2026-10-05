#!/usr/bin/env bash
# Selects the real login accounts install.sh should provision Eggie's agent
# files into. Reads `getent passwd` lines on stdin and prints one
# `name:uid:gid:home` line per account that looks like a person, not a system
# service -- Lima's guest user carries the macOS host uid (often 501, always
# below 1000), so the old `uid >= 1000` bound alone missed it, and simply
# lowering that bound would sweep in system accounts too.
#   getent passwd | login-users.sh <shells-file>
set -euo pipefail

SHELLS=$1

while IFS=: read -r name _ uid gid _ home shell; do
  (( uid >= 500 && uid < 60000 )) || continue
  [[ "$home" != "/" && -d "$home" ]] || continue
  [[ "$shell" != *nologin && "$shell" != *false ]] || continue
  grep -qxF "$shell" "$SHELLS" || continue
  echo "$name:$uid:$gid:$home"
done
