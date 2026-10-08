from __future__ import annotations

API_PORT = 39099
GUEST_ROOT = "/opt/eggie"
GUEST_PROJECTS = f"{GUEST_ROOT}/projects"
GUEST_TOKEN = f"{GUEST_ROOT}/api.token"
GUEST_STACK = f"{GUEST_ROOT}/stack.yml"
COMPOSE_FILE = "docker-compose.yml"
# The code the API answers when it has no usable token of its own.
API_UNCONFIGURED = "api_unconfigured"
# Compose accepts these too; Eggie does not, so a project written under one of
# them must be named, not reported as if it had no compose file at all.
ALT_COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml")
# The API container's only credential shared with this VM.
DOCKER_GROUP = "docker"
# Reserved for the setup smoke test; install.verify_step deletes the project
# through the API but leaves its folder behind, so this keeps it out of the
# "Not set up yet" list on every fresh VM.
VERIFY_PROJECT_ID = "eggie-selftest"
