#!/usr/bin/env bash
# Runs the runtime update when the API asked for one by bumping `request`,
# and records how it went in status.json. Run as root by eggie-update-now.service.
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
  chmod 644 "$tmp"
  mv "$tmp" "$DIR/status.json"
}

n="$(read_int "$DIR/request" "")"
handled="$(read_int "$DIR/status.json" request)"
(( n > handled )) || exit 0

write_status "$n" running
err="$(mktemp)"
trap 'rm -f "$err"' EXIT
if bash "$LIB/boot-update.sh" 2> "$err"; then
  state=done
  reason=""
else
  state=failed
  reason="$(grep -v '^[[:space:]]*$' "$err" | tail -n 1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | cut -c1-300)"
  [[ -n "$reason" ]] || reason="no reason given"
fi
# Replayed after the run so the temp file is complete; the journal still gets it.
cat "$err" >&2
write_status "$n" "$state" "$reason"
# After a swap, $LIB is the new release's copy.
bash "$LIB/update-check.sh" "$DIR" || true
