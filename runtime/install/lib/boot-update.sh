#!/usr/bin/env bash
# Run at boot by eggie-update.service: moves this VM to the newest runtime
# release its host accepts. Any failure leaves the installed runtime running.
set -euo pipefail

# accepted_api: the api numbers the host last declared, else the installed
# release's own -- a VM with no host never changes api.
accepted_api() {
  local api=""
  if [[ -s /opt/eggie/host.json ]]; then
    api="$(tr -d ' \n' < /opt/eggie/host.json | sed -n 's/.*"supported_api":\[\([0-9,]*\)\].*/\1/p')"
  fi
  if [[ ! "$api" =~ ^[0-9]+(,[0-9]+)*$ && -s /opt/eggie/runtime/release.json ]]; then
    api="$(tr -d ' \n' < /opt/eggie/runtime/release.json | sed -n 's/.*"api":\([0-9][0-9]*\).*/\1/p')"
  fi
  [[ "$api" =~ ^[0-9]+(,[0-9]+)*$ ]] || return 1
  echo "$api"
}

main() {
  local installed api script attempt
  installed="$(cat /opt/eggie/runtime.version 2>/dev/null || true)"
  # No marker after an interrupted update: get.sh restores this release first.
  [[ -n "$installed" ]] || installed="$(cat /opt/eggie/runtime.prev.version 2>/dev/null || true)"
  # A branch was pinned by hand; moving it to a tag would undo that.
  if [[ ! "$installed" =~ ^runtime-v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "installed runtime '$installed' is not a release; not updating"
    return 0
  fi
  if ! api="$(accepted_api)"; then
    echo "no api list to update against; not updating" >&2
    return 1
  fi
  # shellcheck disable=SC1091
  source /opt/eggie/runtime.env
  # The network may come up after this unit starts.
  for attempt in 1 2 3 4 5 6; do
    script="$(curl -fsSL "$EGGIE_RUNTIME_URL")" && break
    script=""
    sleep 10
  done
  if [[ -z "$script" ]]; then
    echo "could not download the Eggie updater from $EGGIE_RUNTIME_URL" >&2
    return 1
  fi
  env -u EGGIE_RUNTIME_REF -u EGGIE_RUNTIME_REPAIR \
    EGGIE_RUNTIME_UPDATE=1 EGGIE_RUNTIME_API="$api" \
    EGGIE_RUNTIME_URL="$EGGIE_RUNTIME_URL" EGGIE_RUNTIME_REPO="$EGGIE_RUNTIME_REPO" \
    bash -c "$script"
}

[[ -n "${EGGIE_BOOT_UPDATE_SOURCED:-}" ]] || main "$@"
