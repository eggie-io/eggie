#!/usr/bin/env bash
# Builds the two images a VM runs from runtime/stack.yml: omelet-api and
# omelet-web. They release as a pair under the runtime tag's number, which is
# the only place that number lives; .github/workflows/release-runtime.yml
# passes it here.
#
#   packaging/images/build.sh                              # native arch, loaded into local docker
#   packaging/images/build.sh --push --version 0.4.0       # amd64 + arm64, pushed to ghcr
#   packaging/images/build.sh --push --tag dev --only web  # throwaway tag to try in a VM
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
registry="ghcr.io/omelet-app"
platforms="linux/amd64,linux/arm64"

usage() {
  echo "usage: $0 [--push] [--version X.Y.Z] [--tag <tag>] [--only api|web]" >&2
  exit 2
}

push=0
version=""
tag=""
only=""
while (( $# )); do
  case "$1" in
    --push) push=1 ;;
    --version) version="${2:?--version needs a value}"; shift ;;
    --tag) tag="${2:?--tag needs a value}"; shift ;;
    --only)
      only="${2:?--only needs api or web}"; shift
      [[ "$only" == api || "$only" == web ]] || usage
      ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
  shift
done

if [[ -n "$version" && ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "--version must be X.Y.Z, not '$version'" >&2
  exit 2
fi
# A pushed image needs a name a runtime tag or a VM override will ask for.
if (( push )) && [[ -z "$version" && -z "$tag" ]]; then
  echo "--push needs --version (a release) or --tag (a throwaway image)" >&2
  exit 2
fi
# A dev tag still bakes a release number into the api service's /version.
version="${version:-0.0.0}"
tag="${tag:-$version}"

build() {
  local name=$1 context=$2
  shift 2
  local image="$registry/$name:$tag"
  local args=("$@" -t "$image" "$context")
  echo "==> $image"
  if (( push )); then
    # --push, not --load: the local image store cannot hold a multi-arch image.
    docker buildx build --platform "$platforms" --push "${args[@]}"
  else
    docker buildx build --load "${args[@]}"
  fi
}

[[ "$only" == web ]] || build omelet-api "$repo/runtime/omelet_api" --build-arg "SERVICE_VERSION=$version"
[[ "$only" == api ]] || build omelet-web "$repo/runtime/web" --build-context "fixtures=$repo/tests/fixtures"

if (( push )); then
  echo
  echo "Pushed $tag. A package pushed for the first time is private on ghcr:"
  echo "make it public, or every VM install fails pulling it."
fi
