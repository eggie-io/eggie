#!/usr/bin/env bash
# Run as root before imaging a server. Strips what makes an installed runtime
# this machine's: every droplet made from the image must start with its own
# API token, database, certificate and cloud-init run. The runtime tree,
# the images, .env and runtime.env stay; the next boot's get.sh (repair mode,
# with the site names) finishes the job.
set -euo pipefail

ROOT=/opt/eggie
ROOT_HOME=/root

# `down`, not `stop`: a stopped container with `restart: always` is restarted
# by dockerd at boot, before cloud-init has configured anything.
if [[ -f "$ROOT/stack.yml" ]]; then
  /usr/bin/docker compose -f "$ROOT/stack.yml" --profile tunnel down --remove-orphans
fi
rm -f "$ROOT/api.token" "$ROOT/state.db" "$ROOT/state.db-wal" "$ROOT/state.db-shm" \
      "$ROOT/connect.json" "$ROOT/site.env" "$ROOT/account.json"
rm -rf "$ROOT/tls" "$ROOT/tunnel" "$ROOT/uploads"
# The image builder's key and history must not reach every customer's server;
# the service installs the user's key on the new droplet.
rm -f "$ROOT_HOME/.ssh/authorized_keys" "$ROOT_HOME/.bash_history"
cloud-init clean --logs --machine-id
echo "ready to snapshot: power off and take the image"
