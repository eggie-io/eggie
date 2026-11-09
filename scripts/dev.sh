#!/usr/bin/env bash
# Dev loop on macOS (Windows: scripts/dev.ps1): run this checkout's runtime in
# the Eggie VM and this checkout's host window against it. Nothing is pushed;
# images are built in the VM from your working tree. Re-run after every change.
#
#   scripts/dev.sh                 # fast: sync runtime/, rebuild api + web, restart them, open the window
#   scripts/dev.sh --full          # also run the whole install.sh (CLI, agents, skills, systemd units);
#                                  # creates the VM first if there is none
#   scripts/dev.sh --only web      # rebuild one image (api|web)
#   scripts/dev.sh --no-window     # update the VM only
#   scripts/dev.sh --release       # put the latest released runtime back
#
# The VM records the runtime as `local`. The host only installs when that
# marker is missing, and the boot updater skips anything not a release tag, so
# nothing replaces your dev runtime until you run --release.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
EGGIE="$ROOT/.venv/bin/eggie"
TAG=local
STAGE=/opt/eggie/dev    # mirror of the checkout parts build.sh needs

usage() {
  sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

full=0 window=1 only="" release=0
while (( $# )); do
  case "$1" in
    --full) full=1 ;;
    --no-window) window=0 ;;
    --only)
      only="${2:-}"; shift
      [[ "$only" == api || "$only" == web ]] || usage ;;
    --release) release=1 ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
  shift
done

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'dev.sh: %s\n' "$*" >&2; exit 1; }

[[ -x "$PY" ]] || die "no .venv here; see docs/development.md to create it"

# The same limactl and VM name the app uses (a managed Lima, not Homebrew's).
read -r LIMACTL VM < <("$PY" -c '
from host.providers import get_provider
p = get_provider()
print(p.limactl, p.name)') || die "could not resolve the VM provider (macOS only)"

vm() { "$LIMACTL" shell --workdir / "$VM" "$@"; }
vm_root() { vm sudo bash -c "$1"; }

if (( release )); then
  say "Installing the latest released runtime"
  "$PY" -c '
from host.providers import get_provider
from host.core.install import update_runtime
update_runtime(get_provider())'
  say "Done. The VM runs $(vm cat /opt/eggie/runtime.version)"
  exit 0
fi

if pgrep -f 'Eggie.app/Contents/MacOS' >/dev/null; then
  echo "warning: the installed Eggie app is running. Quit it first: its Quit stops this same VM." >&2
fi

# --- the VM ------------------------------------------------------------------
if ! "$LIMACTL" list --quiet | grep -qx "$VM"; then
  (( full )) || die "no VM '$VM' yet; run: scripts/dev.sh --full"
  say "No VM yet: creating it with the released runtime first (several minutes)"
  "$EGGIE" setup --headless
elif [[ "$("$LIMACTL" list --format '{{.Status}}' "$VM")" != Running ]]; then
  say "Starting VM $VM"
  "$EGGIE" vm start
fi

vm test -x /usr/bin/docker \
  || die "the VM has no Docker yet; run: $EGGIE setup --headless, then this again"

# --- sync the checkout into the VM -------------------------------------------
say "Copying runtime/ into the VM"
# COPYFILE_DISABLE: no macOS ._ files in the archive.
COPYFILE_DISABLE=1 tar -C "$ROOT" -czf - \
    --exclude node_modules --exclude dist --exclude __pycache__ \
    --exclude .vitest --exclude .DS_Store \
    runtime tests/fixtures packaging/images/build.sh \
  | vm sudo bash -c "
      set -e
      rm -rf $STAGE && mkdir -p $STAGE
      tar -xzf - --no-same-owner -C $STAGE
      rm -rf /opt/eggie/runtime
      cp -a $STAGE/runtime /opt/eggie/runtime
      chmod 755 /opt/eggie/runtime"

# --- images ------------------------------------------------------------------
say "Building ${only:-api + web} images in the VM (tag $TAG)"
vm_root "bash $STAGE/packaging/images/build.sh --tag $TAG ${only:+--only $only}"

# --- install / restart -------------------------------------------------------
if (( full )); then
  say "Running install.sh from your checkout"
  vm_root "flock /opt/eggie/update.lock env EGGIE_IMAGE_VERSION=$TAG EGGIE_SKIP_PULL=1 \
             bash /opt/eggie/runtime/install/install.sh $TAG"
else
  say "Restarting the stack on the local images"
  # What install.sh's steps 5, 7 and the CLI step do, minus everything slow.
  vm_root "
    set -e
    sed -i 's/^EGGIE_VERSION=.*/EGGIE_VERSION=$TAG/' /opt/eggie/.env
    install -m 644 /opt/eggie/runtime/stack.yml /opt/eggie/stack.yml
    /usr/bin/docker compose -f /opt/eggie/stack.yml up -d
    cli=\$(mktemp)
    python3 -m zipapp /opt/eggie/runtime/cli -m eggie_cli.cli:run -p '/usr/bin/env python3' -o \$cli
    install -m 755 \$cli /usr/local/bin/eggie && rm -f \$cli
    echo $TAG > /opt/eggie/runtime.version"
fi

# --- window ------------------------------------------------------------------
if (( window )); then
  say "Opening the desktop window from this checkout (Ctrl-C to close)"
  cd "$ROOT"
  exec "$PY" -m host.desktop
fi
say "VM updated. Open the window with: .venv/bin/python -m host.desktop"
