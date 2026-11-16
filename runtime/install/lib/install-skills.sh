#!/usr/bin/env bash
# Installs Eggie's skills into one account with the skills CLI, as that user.
# Run by install.sh as root, once per account:
#   install-skills.sh <name> <home> <skills-cli> <skills-source> <agent>...
set -euo pipefail

NAME=$1
HOME_DIR=$2
CLI=$3
SOURCE=$4
shift 4

as_user() {
  runuser -u "$NAME" -- env HOME="$HOME_DIR" "$@"
}

# Every new VM depends on a clean download from the npm registry. A failed
# download leaves a half-installed package under the user's ~/.npm/_npx,
# which npx reuses as is; `npm cache clean` does not touch that directory.
for attempt in 1 2 3; do
  # stdin is install.sh's account list: npx must not get to read it.
  if as_user DISABLE_TELEMETRY=1 npx -y "$CLI" add "$SOURCE" -s '*' -g -a "$@" -y </dev/null; then
    exit 0
  fi
  if (( attempt < 3 )); then
    echo "installing Eggie's skills for $NAME failed (attempt $attempt of 3); retrying" >&2
    as_user rm -rf -- "$HOME_DIR/.npm/_npx" </dev/null || true
    sleep 5
  fi
done

echo "could not install Eggie's skills for $NAME: the npm registry or GitHub may be unreachable" >&2
exit 1
