#!/usr/bin/env bash
# The runtime's entrypoint: choose a runtime ref, unpack that ref's runtime/
# into /opt/omelet/runtime and run its install/install.sh. Fetched on its own
# (the host's bootstrap, or `curl -fsSL <url> | sudo bash` on a cloud VM), so
# it can rely on nothing beside it. Run as root.
set -euo pipefail
# A private or misspelled repo must fail outright, not hang on a credential
# prompt nobody is watching.
export GIT_TERMINAL_PROMPT=0

REPO="${OMELET_RUNTIME_REPO:-https://github.com/ihorklymchukdev/local-environment}"
MARKER=/opt/omelet/runtime.version
RUNTIME_DIR=/opt/omelet/runtime
# Script-scoped, not local to main: the EXIT trap below runs after main
# returns, once main's own locals are already out of scope.
tmp=

# release_api <repo> <tag>: the api number a release declares; empty when it
# declares none (every release cut before release.json existed).
release_api() {
  curl -fsSL "$1/raw/$2/runtime/release.json" 2>/dev/null \
    | tr -d ' \n' | sed -n 's/.*"api":\([0-9][0-9]*\).*/\1/p'
}

# resolve_ref <repo> <marker>: an explicit ref wins, a repair keeps what is
# installed, anything else takes the highest runtime-v* tag -- the highest one
# speaking an api in OMELET_RUNTIME_API when that is set.
resolve_ref() {
  local repo=$1 marker=$2 tags candidates tag api
  if [[ -n "${OMELET_RUNTIME_REF:-}" ]]; then
    echo "$OMELET_RUNTIME_REF"
    return
  fi
  if [[ "${OMELET_RUNTIME_REPAIR:-}" == 1 && -s "$marker" ]]; then
    cat "$marker"
    return
  fi
  if [[ -n "${OMELET_RUNTIME_API:-}" && ! "$OMELET_RUNTIME_API" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    echo "OMELET_RUNTIME_API must be a comma-separated list of numbers, not '$OMELET_RUNTIME_API'" >&2
    return 1
  fi
  if ! tags="$(git ls-remote --tags --refs "$repo" 'runtime-v*')"; then
    echo "could not reach $repo to find the latest Omelet runtime" >&2
    return 1
  fi
  # grep exits 1 when no tag matches; the empty result is handled below.
  candidates="$(sed -n 's#.*refs/tags/##p' <<<"$tags" | grep -E '^runtime-v[0-9]+\.[0-9]+\.[0-9]+$' | sort -rV)" || true
  if [[ -z "$candidates" ]]; then
    echo "$repo has no runtime-v* release to install" >&2
    return 1
  fi
  if [[ -z "${OMELET_RUNTIME_API:-}" ]]; then
    head -n 1 <<<"$candidates"
    return
  fi
  while read -r tag; do
    api="$(release_api "$repo" "$tag")" || true
    if [[ -n "$api" && ",$OMELET_RUNTIME_API," == *",$api,"* ]]; then
      echo "$tag"
      return
    fi
  done <<<"$candidates"
  echo "no runtime-v* release in $repo speaks api $OMELET_RUNTIME_API" >&2
  return 1
}

main() {
  export DEBIAN_FRONTEND=noninteractive
  local missing=() pkg
  for pkg in ca-certificates curl git; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  if (( ${#missing[@]} )); then
    if ! apt-get update || ! apt-get install -y "${missing[@]}"; then
      echo "could not install ${missing[@]}: the Ubuntu package mirrors may be unreachable" >&2
      exit 1
    fi
  fi

  local ref
  ref="$(resolve_ref "$REPO" "$MARKER")"
  echo "installing Omelet runtime $ref"

  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp:-}"' EXIT
  if ! curl -fsSL "$REPO/archive/$ref.tar.gz" -o "$tmp/runtime.tar.gz"; then
    echo "could not download Omelet runtime $ref from $REPO" >&2
    exit 1
  fi
  if ! tar -xzf "$tmp/runtime.tar.gz" -C "$tmp" --strip-components=1 --wildcards '*/runtime/' 2>/dev/null; then
    echo "$ref of $REPO is not a readable archive or has no runtime/ directory" >&2
    exit 1
  fi
  if [[ ! -f "$tmp/runtime/install/install.sh" ]]; then
    echo "$ref of $REPO has no runtime/install/install.sh" >&2
    exit 1
  fi
  # Replaced, not merged: a file dropped from the runtime must not linger.
  mkdir -p /opt/omelet
  rm -rf "$RUNTIME_DIR"
  mv "$tmp/runtime" "$RUNTIME_DIR"
  chmod 755 "$RUNTIME_DIR"

  local args=("$ref")
  if [[ "${OMELET_RUNTIME_REPAIR:-}" == 1 ]]; then
    args+=(--repair)
  fi
  bash "$RUNTIME_DIR/install/install.sh" "${args[@]}"
}

# `return` only succeeds when sourced (the tests); under `bash -c` or a pipe it
# fails and the install runs.
(return 0 2>/dev/null) || main "$@"
