#!/usr/bin/env bash
# Containers must not reach the cloud metadata service: on a droplet it
# serves the user data that seeded this machine's account token.
# DOCKER-USER is the chain dockerd leaves to the administrator: created
# if missing, jumped to from FORWARD, never flushed.
set -euo pipefail

METADATA=169.254.169.254

iptables -w -N DOCKER-USER 2>/dev/null || true
if ! iptables -w -C DOCKER-USER -d "$METADATA" -j DROP 2>/dev/null; then
  iptables -w -I DOCKER-USER -d "$METADATA" -j DROP
fi
