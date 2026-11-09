#!/usr/bin/env bash
# Records in available.json whether a newer runtime release the host accepts exists.
#   update-check.sh [update-dir]
set -euo pipefail

DIR="${1:-/opt/eggie/update}"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

write_available() {
  local ref="$1" tmp
  tmp="$(mktemp "$DIR/.available.XXXXXX")"
  python3 -c 'import json,sys,time
ref=sys.argv[1]
print(json.dumps({"available": ref or None, "checked_at": int(time.time())}))' "$ref" > "$tmp"
  chmod 640 "$tmp"
  mv "$tmp" "$DIR/available.json"
}

check_main() {
  local installed api ref
  installed="$(cat /opt/eggie/runtime.version 2>/dev/null || true)"
  # A pinned branch is never moved to a tag, so there is nothing to offer.
  if [[ ! "$installed" =~ ^runtime-v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    write_available ""
    return 0
  fi
  # shellcheck disable=SC1091
  EGGIE_BOOT_UPDATE_SOURCED=1 source "$LIB/boot-update.sh"
  api="$(accepted_api)"
  # shellcheck disable=SC1091
  source /opt/eggie/runtime.env
  # shellcheck disable=SC1091
  source "$LIB/../get.sh"
  unset EGGIE_RUNTIME_REF EGGIE_RUNTIME_REPAIR
  ref="$(EGGIE_RUNTIME_API="$api" resolve_ref "$EGGIE_RUNTIME_REPO" /dev/null)"
  # A failed release.json fetch lands resolve_ref on an older tag; that says
  # nothing about what is available, so keep the last answer.
  if [[ "$ref" != "$installed" \
        && "$(printf '%s\n' "$installed" "$ref" | sort -V | tail -n 1)" == "$installed" ]]; then
    echo "resolved $ref, older than installed $installed; keeping the last answer" >&2
    return 1
  fi
  if [[ "$ref" != "$installed" \
        && "$(printf '%s\n' "$installed" "$ref" | sort -V | tail -n 1)" == "$ref" ]]; then
    write_available "$ref"
  else
    write_available ""
  fi
}

check_main
