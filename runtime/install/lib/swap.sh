#!/usr/bin/env bash
# Adds a swapfile to a VM that has no swap. Lima and cloud images ship without
# one, so a VM short of RAM thrashes its page cache and pegs the CPU instead of
# slowing down. WSL already has swap and is left alone by the same check.
# Run as root by install.sh.
#   swap.sh <swapfile> <fstab>
set -euo pipefail

SWAPFILE=$1
FSTAB=$2
SIZE_GB=4
# Never trade the disk projects need for swap.
MIN_FREE_GB=$((SIZE_GB + 10))

if [[ -n "$(swapon --show --noheadings)" ]]; then
  exit 0
fi

if [[ ! -f "$SWAPFILE" ]]; then
  free_gb=$(df --output=avail -BG "$(dirname "$SWAPFILE")" | tail -1 | tr -dc 0-9)
  if (( free_gb < MIN_FREE_GB )); then
    echo "not adding swap: ${free_gb} GB free, ${MIN_FREE_GB} GB needed" >&2
    exit 0
  fi
  staged="$SWAPFILE.new"
  trap 'rm -f "$staged"' EXIT
  fallocate -l "${SIZE_GB}G" "$staged"
  chmod 600 "$staged"
  mkswap "$staged" >/dev/null
  mv "$staged" "$SWAPFILE"
fi
swapon "$SWAPFILE"

if ! grep -qs "^$SWAPFILE " "$FSTAB"; then
  echo "$SWAPFILE none swap sw 0 0" >> "$FSTAB"
fi
