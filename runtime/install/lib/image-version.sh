#!/usr/bin/env bash
# image-version.sh <ref> <repo>: the omelet-api/omelet-web tag a runtime ref
# runs. A release runs its own number; any other ref (a branch being tried in a
# VM) runs the newest release's images unless OMELET_IMAGE_VERSION names others.
set -euo pipefail

ref=${1:?usage: image-version.sh <runtime ref> <repo>}
repo=${2:?usage: image-version.sh <runtime ref> <repo>}

if [[ "$ref" =~ ^runtime-v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  echo "${BASH_REMATCH[1]}"
  exit 0
fi
if [[ -n "${OMELET_IMAGE_VERSION:-}" ]]; then
  echo "$OMELET_IMAGE_VERSION"
  exit 0
fi
if ! tags="$(git ls-remote --tags --refs "$repo" 'runtime-v*')"; then
  echo "could not reach $repo to find which images $ref runs" >&2
  exit 1
fi
latest="$(sed -n 's#.*refs/tags/runtime-v##p' <<<"$tags" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n 1)" || true
if [[ -z "$latest" ]]; then
  echo "$repo has no runtime-v* release whose images $ref could run" >&2
  exit 1
fi
echo "$latest"
