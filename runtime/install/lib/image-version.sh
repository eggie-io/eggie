#!/usr/bin/env bash
# image-version.sh <ref> <repo>: the eggie-api/eggie-web tag a runtime ref
# runs. A release runs its own number. Any other ref (a branch being tried in
# a VM) runs EGGIE_IMAGE_VERSION when set, else images pushed under its own
# name (build.sh --push --tag <ref>) when ghcr has both, else the newest
# release's.
set -euo pipefail

ref=${1:?usage: image-version.sh <runtime ref> <repo>}
repo=${2:?usage: image-version.sh <runtime ref> <repo>}

if [[ "$ref" =~ ^runtime-v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  echo "${BASH_REMATCH[1]}"
  exit 0
fi
if [[ -n "${EGGIE_IMAGE_VERSION:-}" ]]; then
  echo "$EGGIE_IMAGE_VERSION"
  exit 0
fi

# The HTTP status ghcr gives an anonymous pull of <image>:<tag>; 000 when it
# gave none. A private package answers like a missing one.
manifest_status() {
  local image=$1 tag=$2 token status
  token="$(curl -fsS "https://ghcr.io/token?service=ghcr.io&scope=repository:eggie-io/$image:pull" 2>/dev/null \
           | sed -n 's/.*"token" *: *"\([^"]*\)".*/\1/p')" || token=""
  if [[ -z "$token" ]]; then
    echo 000
    return
  fi
  # -w prints the status even when curl itself fails, so its exit is ignored.
  status="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $token" \
    -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
    "https://ghcr.io/v2/eggie-io/$image/manifests/$tag" 2>/dev/null)" || true
  echo "${status:-000}"
}

# Only a ref that is a valid image tag (dev, not feature/x) can have its own.
if [[ "$ref" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  own=1
  for image in eggie-api eggie-web; do
    case "$(manifest_status "$image" "$ref")" in
      200) ;;
      000)
        echo "could not reach ghcr.io to see whether $ref has its own images" >&2
        exit 1
        ;;
      *) own=0; break ;;
    esac
  done
  if (( own )); then
    echo "$ref"
    exit 0
  fi
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
