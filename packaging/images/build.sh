#!/usr/bin/env bash
# Builds the two images a VM runs from runtime/stack.yml: omelet-api and
# omelet-web. They release as a pair under runtime/omelet_api/__init__.py's version.
#
#   packaging/images/build.sh                 # native arch, loaded into local docker
#   packaging/images/build.sh --push          # amd64 + arm64, pushed to ghcr
#   packaging/images/build.sh --tag dev --push --only web
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
registry="ghcr.io/omelet-app"
platforms="linux/amd64,linux/arm64"

usage() {
  echo "usage: $0 [--push] [--tag <tag>] [--only api|web]" >&2
  exit 2
}

push=0
tag=""
only=""
while (( $# )); do
  case "$1" in
    --push) push=1 ;;
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

version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$repo/runtime/omelet_api/__init__.py")"
dockerfile_version="$(sed -n 's/^ARG SERVICE_VERSION=//p' "$repo/runtime/omelet_api/Dockerfile")"
stack_api="$(sed -n 's/.*omelet-api:\([^}]*\)}.*/\1/p' "$repo/runtime/stack.yml")"
stack_web="$(sed -n 's/.*omelet-web:\([^}]*\)}.*/\1/p' "$repo/runtime/stack.yml")"

# A mismatch here publishes an image no runtime tag will ever pull, or an
# api service whose /version disagrees with the tag the VM asked for.
if [[ -z "$version" || "$version" != "$dockerfile_version" \
      || "$version" != "$stack_api" || "$version" != "$stack_web" ]]; then
  echo "versions disagree; bump them together before building:" >&2
  echo "  runtime/omelet_api/__init__.py $version" >&2
  echo "  runtime/omelet_api/Dockerfile  $dockerfile_version" >&2
  echo "  runtime/stack.yml api  $stack_api" >&2
  echo "  runtime/stack.yml web  $stack_web" >&2
  exit 1
fi

# A dev tag still bakes the release version into the api service, so
# /version reports what the source says, not "dev".
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
