#!/usr/bin/env bash
# tunnel-recreate.sh [-f <compose file>]...: puts a running tunnel client on
# the image stack.yml names now. The installer's `up -d` skips the profiled
# service and the API leaves a running client alone, so without this an
# update keeps the old cloudflared for as long as the public URL stays on.
set -euo pipefail

compose=(/usr/bin/docker compose "$@" --profile tunnel)
if [[ -z "$("${compose[@]}" ps -q tunnel 2>/dev/null || true)" ]]; then
  exit 0
fi
# Not fatal: the API may start the client at the same moment from the same
# stack.yml, and failing the install would roll the update back to the old image.
if ! "${compose[@]}" up -d --no-deps --force-recreate tunnel; then
  echo "could not restart the public URL client on its new image; turning the public URL off and on again restarts it" >&2
fi
