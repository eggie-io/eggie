#!/usr/bin/env bash
# Runs the runtime update when the API bumps `request`; records the outcome in status.json.
#   update-now.sh [update-dir]
set -uo pipefail

DIR="${1:-/opt/eggie/update}"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# read_int <file> <json-key>: the integer, or 0 when the file is missing or garbage.
read_int() {
  python3 -c 'import json,sys
try:
    text = open(sys.argv[1]).read()
    v = json.loads(text)[sys.argv[2]] if sys.argv[2] else int(text.strip())
    print(v if isinstance(v, int) and v > 0 else 0)
except Exception:
    print(0)' "$1" "$2"
}

write_status() {
  local tmp
  tmp="$(mktemp "$DIR/.status.XXXXXX")"
  python3 -c 'import json,sys
print(json.dumps({"request": int(sys.argv[1]), "state": sys.argv[2], "error": sys.argv[3] or None}, separators=(",", ":")))' \
    "$1" "$2" "${3:-}" > "$tmp"
  chmod 640 "$tmp"
  mv "$tmp" "$DIR/status.json"
}

n="$(read_int "$DIR/request" "")"
handled="$(read_int "$DIR/status.json" request)"
(( n > handled )) || exit 0

err="$(mktemp)"
trap 'rm -f "$err"' EXIT

# The API accepts a new request as soon as status leaves "running", and
# systemd drops path triggers that arrive while this unit is active.
for _ in 1 2 3 4 5; do
  n="$(read_int "$DIR/request" "")"
  handled="$(read_int "$DIR/status.json" request)"
  (( n > handled )) || break

  write_status "$n" running || exit 1
  bash "$LIB/boot-update.sh" 2> >(tee "$err" >&2)
  rc=$?
  wait # the tee must finish before the file is read
  if (( rc == 0 )); then
    state=done
    reason=""
  else
    state=failed
    # get.sh rolls back and re-runs the old install, whose output would otherwise be the last line.
    reason="$(grep 'did not install; going back to' "$err" | tail -n 1)"
    [[ -n "$reason" ]] || reason="$(grep -v '^[[:space:]]*$' "$err" | tail -n 1)"
    reason="$(sed 's/^[[:space:]]*//;s/[[:space:]]*$//' <<<"$reason" | cut -c1-300)"
    [[ -n "$reason" ]] || reason="no reason given"
  fi
  write_status "$n" "$state" "$reason"
  # After a swap, $LIB is the new release's copy.
  bash "$LIB/update-check.sh" "$DIR" || true
done
