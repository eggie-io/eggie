"""Host-side constants.

Deliberately separate from `omelet_api/core/constants.py`: nothing under `host/`
may import `omelet_api/`. Names appearing in both modules are held equal by
`tests/test_constants_agree.py` -- that test, not a shared import, is what stops
the two copies drifting.

Guest paths are derived from GUEST_ROOT rather than spelled out, and the runtime
shell tests compare install.sh's literals against these names, so the host and
the runtime cannot end up pointing at different files.
"""

# The desktop app's own version, shown on the status screen and in the
# diagnostics text. Bumped with pyproject.toml's.
APP_VERSION = "0.1.0"

# Desktop app releases are tagged host-vX.Y.Z on this repository.
HOST_RELEASES_URL = "https://api.github.com/repos/omelet-app/omelet/releases"

GUEST_ROOT = "/opt/omelet"
GUEST_PROJECTS = f"{GUEST_ROOT}/projects"
API_PORT = 39099
# Forwarded by the providers, not used to build URLs on the host: the api
# service's payloads carry every URL the CLI prints.
EDGE_PORT = 39080
# The host only needs the domain to hand the installer's smoke test a value;
# every project URL it prints comes from the api service's own payload.
DEFAULT_DOMAIN = "127-0-0-1.sslip.io"
VERIFY_PROJECT_ID = "omelet-selftest"
# The api service decides what a project must contain; the host only needs the
# name to refuse an empty folder before uploading it. Declared on both sides so
# the constants test fails if the api service ever accepts a second spelling.
COMPOSE_FILE = "docker-compose.yml"

# The host knows only where the runtime's entrypoint lives and which file says
# it finished; what gets installed, and which version, is decided in the VM.
RUNTIME_URL = ("https://raw.githubusercontent.com/omelet-app/"
               "omelet/main/runtime/install/get.sh")
RUNTIME_MARKER = f"{GUEST_ROOT}/runtime.version"

# Written by the host on every connect; the VM's boot-time updater reads it to
# stay on releases this host can drive.
HOST_JSON = f"{GUEST_ROOT}/host.json"

# Generated in the guest by the runtime installer, never pushed from the host.
# The host reads it fresh per client via provider.exec(root=True) rather than
# caching a copy -- see host/client.py.
GUEST_TOKEN = f"{GUEST_ROOT}/api.token"

# The api service's API numbers this host can drive. A runtime release that
# keeps the routes compatible keeps the number, so it never needs a host
# release.
SUPPORTED_API = frozenset({1})

# The code the api service answers when it has no usable token of its own.
# Declared on both sides so a rename on one side without the other fails
# test_constants_agree instead of silently breaking connect_step's repair path.
API_UNCONFIGURED = "api_unconfigured"
