#!/usr/bin/env bash
# Gives a WSL VM a non-root login account and makes it the one `wsl -d` opens.
# WSL distros come with root only, and Claude Code refuses to skip its
# permission prompts as root. Run as root by install.sh, WSL only.
#   wsl-user.sh <wsl.conf> <sudoers.d dir>
set -euo pipefail

WSL_CONF=$1
SUDOERS_DIR=$2
NAME=eggie

if ! id -u "$NAME" >/dev/null 2>&1; then
  # Never 1000: that is the API container's uid, and files it writes into
  # projects would then belong to this account.
  useradd -m -s /bin/bash -K UID_MIN=1001 -K GID_MIN=1001 "$NAME"
fi

# Same reach as root already has through the docker group.
rule="$NAME ALL=(ALL) NOPASSWD:ALL"
staged="$(mktemp)"
echo "$rule" > "$staged"
visudo -cqf "$staged"
install -m 440 "$staged" "$SUDOERS_DIR/$NAME"
rm -f "$staged"

# A [user] section already there is someone's choice; leave it. WSL reads this
# at boot, so the new default applies from the VM's next start.
if ! grep -qx '\[user\]' "$WSL_CONF" 2>/dev/null; then
  printf '\n[user]\ndefault=%s\n' "$NAME" >> "$WSL_CONF"
fi
