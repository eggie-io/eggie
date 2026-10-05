#!/usr/bin/env bash
# Applies the API's GitHub desired state to every login account: gh signed in
# or out, and git's identity. Run as root by eggie-github.service and once by
# install.sh; re-running it is safe.
#   github-apply.sh [github-dir]
# Not -e: one account failing must not leave the others unapplied.
set -uo pipefail

DIR="${1:-/opt/eggie/github}"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESIRED="$DIR/desired.json"
APPLIED="$DIR/applied.json"
TOKEN="$DIR/token"
ROOT_HOME="${EGGIE_ROOT_HOME:-/root}"
SHELLS="${EGGIE_SHELLS_FILE:-/etc/shells}"
SAFE_PATH="${EGGIE_APPLY_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
MAX_PASSES=5

field() {
  python3 -c 'import json, sys
try:
    v = json.load(open(sys.argv[1])).get(sys.argv[2])
except (OSError, ValueError):
    v = None
print("" if v is None else v)' "$1" "$2"
}

write_applied() {
  python3 - "$APPLIED.tmp" "$@" <<'PY'
import json, sys
out, gen, ok, error, name, email, login, *rows = sys.argv[1:]
accounts = [{"name": r.rsplit(":", 1)[0], "ok": r.rsplit(":", 1)[1] == "ok"} for r in rows]
with open(out, "w") as f:
    json.dump({"generation": int(gen), "ok": ok == "1", "error": error or None,
               "name": name or None, "email": email or None,
               "login": login or None, "accounts": accounts}, f)
PY
  chmod 644 "$APPLIED.tmp" && mv -f "$APPLIED.tmp" "$APPLIED"
}

# Before this feature a user may have run `gh auth login` by hand; with no
# desired state yet there is nothing of ours to apply or undo.
if [[ ! -f "$DESIRED" ]]; then
  write_applied 0 1 "" "" "" ""
  exit 0
fi

accounts() {
  echo "root:0:0:$ROOT_HOME"
  getent passwd | bash "$LIB/login-users.sh" "$SHELLS"
}

as_user() {
  local name=$1 home=$2
  shift 2
  # No -i: runuser without -l already carries the caller's environment, and
  # wiping it here would drop nothing worth dropping while overriding HOME
  # and PATH is what actually keeps gh/git pointed at the right account.
  runuser -u "$name" -- env HOME="$home" PATH="$SAFE_PATH" \
    GH_PROMPT_DISABLED=1 GH_NO_UPDATE_NOTIFIER=1 "$@"
}

# gh's exact wording for "there was nothing of ours to log out of" varies by
# version, so match loosely and case-insensitively rather than pin one phrase.
logout_was_a_noop() {
  local lower=${1,,}
  [[ "$lower" == *"not logged in"* || "$lower" == *"no account"* ]]
}

connect() {
  local name=$1 home=$2 out
  [[ -n "$SECRET" ]] || { echo "the token file is missing"; return 1; }
  # gh 2.40+ keeps several accounts per host, so signing in as a new login
  # leaves the previous one still active too. Log it out first, or a later
  # disconnect (which only logs out the *current* login) would leave it live.
  if [[ -n "$PREV_LOGIN" && "$PREV_LOGIN" != "$LOGIN" ]]; then
    out="$(as_user "$name" "$home" gh auth logout --hostname github.com \
      --user "$PREV_LOGIN" 2>&1)"
    if (( $? != 0 )) && ! logout_was_a_noop "$out"; then
      printf '%s\n' "$out"
      return 1
    fi
  fi
  as_user "$name" "$home" gh auth login --hostname github.com \
      --git-protocol https --insecure-storage --with-token < "$TOKEN" &&
    as_user "$name" "$home" gh auth setup-git --hostname github.com &&
    as_user "$name" "$home" git config --global user.name "$NAME" &&
    as_user "$name" "$home" git config --global user.email "$EMAIL"
}

unset_if_ours() {
  local name=$1 home=$2 key=$3 ours=$4
  [[ -n "$ours" ]] || return 0
  if [[ "$(as_user "$name" "$home" git config --global --get "$key")" == "$ours" ]]; then
    as_user "$name" "$home" git config --global --unset "$key"
  fi
}

disconnect() {
  local name=$1 home=$2 out
  # Since gh 2.40 one host can hold several accounts (a hand sign-in, or a
  # reconnect as someone else), so a plain logout can fail asking for --user.
  # Only log out the account we recorded signing in; nothing to undo
  # otherwise, and that also leaves a hand sign-in alone.
  if [[ -n "$PREV_LOGIN" ]]; then
    out="$(as_user "$name" "$home" gh auth logout --hostname github.com \
      --user "$PREV_LOGIN" 2>&1)"
    if (( $? != 0 )) && ! logout_was_a_noop "$out"; then
      printf '%s\n' "$out"
      return 1
    fi
  fi
  unset_if_ours "$name" "$home" user.name "$PREV_NAME" &&
    unset_if_ours "$name" "$home" user.email "$PREV_EMAIL"
}

PASS=0
while :; do
  PASS=$((PASS + 1))
  SECRET=""
  [[ -r "$TOKEN" ]] && SECRET="$(cat "$TOKEN")"
  GEN="$(field "$DESIRED" generation)"
  STATE="$(field "$DESIRED" state)"
  NAME="$(field "$DESIRED" name)"
  EMAIL="$(field "$DESIRED" email)"
  LOGIN="$(field "$DESIRED" login)"
  PREV_NAME="$(field "$APPLIED" name)"
  PREV_EMAIL="$(field "$APPLIED" email)"
  PREV_LOGIN="$(field "$APPLIED" login)"

  OK=1
  ERROR=""
  ROWS=()
  while IFS=: read -r name _uid _gid home; do
    if [[ "$STATE" == connected ]]; then
      out="$(connect "$name" "$home" 2>&1)"
    else
      out="$(disconnect "$name" "$home" 2>&1)"
    fi
    if (( $? == 0 )); then
      ROWS+=("$name:ok")
    else
      OK=0
      ROWS+=("$name:failed")
      if [[ -z "$ERROR" ]]; then
        ERROR="$name: $(printf '%s' "$out" | tail -n 3)"
        [[ -n "$SECRET" ]] && ERROR="${ERROR//"$SECRET"/[token]}"
      fi
    fi
  done < <(accounts)

  if [[ "$STATE" == connected ]]; then
    write_applied "$GEN" "$OK" "$ERROR" "$NAME" "$EMAIL" "$LOGIN" "${ROWS[@]}"
  elif [[ "$OK" == 0 ]]; then
    # A disconnect that did not fully log out must keep the previous
    # identity, or a retry would no longer know who to log out.
    write_applied "$GEN" "$OK" "$ERROR" "$PREV_NAME" "$PREV_EMAIL" "$PREV_LOGIN" "${ROWS[@]}"
  else
    write_applied "$GEN" "$OK" "$ERROR" "" "" "" "${ROWS[@]}"
  fi

  # A desired.json written while this pass was running (e.g. a quick
  # connect-then-disconnect) is not covered by PathChanged firing again once
  # this run exits, so re-check and, if it moved on, apply it too.
  NEW_GEN="$(field "$DESIRED" generation)"
  if [[ "$NEW_GEN" == "$GEN" || "$PASS" -ge "$MAX_PASSES" ]]; then
    break
  fi
done
