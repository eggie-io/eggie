#!/usr/bin/env bash
# Detects which coding agents have connected and runs the setups the console
# asked for, over root and every login account. Run as root by
# eggie-agents.service and once by install.sh.
#   agents-run.sh [status-dir]
set -euo pipefail

DIR="${1:-/opt/eggie/agent-status}"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS="${EGGIE_AGENTS_DIR:-$LIB/../../agents}"
ROOT_HOME="${EGGIE_ROOT_HOME:-/root}"
SHELLS="${EGGIE_SHELLS_FILE:-/etc/shells}"
SAFE_PATH="${EGGIE_APPLY_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"

accounts="root:0:0:$ROOT_HOME
$(getent passwd | bash "$LIB/login-users.sh" "$SHELLS")"
python3 "$LIB/agents.py" --agents-dir "$AGENTS" run "$DIR" --path "$SAFE_PATH" <<< "$accounts"
