#!/usr/bin/env bash
# Detects which coding agents have connected and runs the setups the console
# asked for, over root and every login account. Run as root by
# omelet-agents.service and once by install.sh.
#   agents-run.sh [status-dir]
set -euo pipefail

DIR="${1:-/opt/omelet/agent-status}"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS="${OMELET_AGENTS_DIR:-$LIB/../../agents}"
ROOT_HOME="${OMELET_ROOT_HOME:-/root}"
SHELLS="${OMELET_SHELLS_FILE:-/etc/shells}"
SAFE_PATH="${OMELET_APPLY_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"

accounts="root:0:0:$ROOT_HOME
$(getent passwd | bash "$LIB/login-users.sh" "$SHELLS")"
python3 "$LIB/agents.py" --agents-dir "$AGENTS" run "$DIR" --path "$SAFE_PATH" <<< "$accounts"
