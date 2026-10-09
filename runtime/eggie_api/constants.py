EDGE_PORT = 39080
API_PORT = 39099
GUEST_ROOT = "/opt/eggie"
GUEST_PROJECTS = f"{GUEST_ROOT}/projects"
EDGE_NETWORK = "edge"
COMPOSE_FILE = "docker-compose.yml"
DEFAULT_DOMAIN = "local.eggie.space"
# Reserved for the setup smoke test. Deriving it from the template directory
# name would let `verify` compose-down a user project that happened to share it.
VERIFY_PROJECT_ID = "eggie-selftest"

# Bumped only when a route the host calls changes incompatibly; the host
# refuses an API whose number it does not list in SUPPORTED_API.
API_VERSION = 1

# Generated in the guest by the runtime installer, never pushed from the host.
GUEST_TOKEN = f"{GUEST_ROOT}/api.token"

GITHUB_DIR = f"{GUEST_ROOT}/github"
AGENT_STATUS_DIR = f"{GUEST_ROOT}/agent-status"
AGENTS_DIR = f"{GUEST_ROOT}/runtime/agents"
# Eggie's OAuth App. Public by design: the Device Flow needs no secret.
GITHUB_CLIENT_ID = "Ov23lie5k9VqSCKI52Ci"

# The code this service answers when it has no usable token of its own.
API_UNCONFIGURED = "api_unconfigured"

CONNECT_FILE = f"{GUEST_ROOT}/connect.json"
# What eggie.yaml asks Lima to forward; Lima has not been seen honouring it.
LIMA_SSH_PORT = 39022
LIMA_KEY_FILE = "~/.lima/_config/user"
