"""The host and the API service each own a constants module (a hard invariant
forbids `host/` importing `omelet_api/`), so a handful of names are declared
twice. Nothing stops the two copies drifting except this test: a guest path or
port that disagrees across the seam produces a VM the host cannot talk to,
with no error naming the cause.
"""
from omelet_api.core import constants as api_constants
from host.core import constants as host_constants


def _public(module) -> dict:
    return {name: value for name, value in vars(module).items()
            if name.isupper()}


def test_names_declared_in_both_constants_modules_hold_the_same_value():
    api = _public(api_constants)
    host = _public(host_constants)
    shared = sorted(set(api) & set(host))
    assert shared, "the two modules share no names -- this test is no longer guarding anything"
    diverged = {name: (host[name], api[name])
                for name in shared if host[name] != api[name]}
    assert not diverged, f"host/api constants diverged (host, api): {diverged}"


def test_the_runtime_entrypoint_constants_live_only_on_the_host():
    # The API has no use for either, and must not become a second source of
    # truth for where the runtime comes from.
    for name in ("RUNTIME_URL", "RUNTIME_MARKER"):
        assert not hasattr(api_constants, name), f"{name} must not live in omelet_api/core/constants.py"
        assert hasattr(host_constants, name), f"{name} must live in host/core/constants.py"


def test_no_host_contract_name_still_says_engine_or_agent():
    # The window for these renames closes with the first shipped host binary,
    # so the test exists to fail loudly if one is reintroduced.
    stale = [name for name in vars(host_constants)
             if name.isupper() and ("ENGINE" in name or "AGENT" in name)]
    assert not stale, f"host constants still carrying the old vocabulary: {stale}"


def test_the_readiness_window_is_the_same_on_both_sides_of_the_seam():
    # Declared twice for the same reason as the constants above: the api
    # service may not import from host/. Both wait out the same behaviour --
    # Traefik publishing a router a beat after the container starts -- so an
    # api service with the shorter window would diagnose a fault the host's
    # own check waits out.
    from omelet_api.core.health import READY_TIMEOUT as api_timeout
    from host.core.install import READY_TIMEOUT as host_timeout

    assert host_timeout == api_timeout


def test_the_stack_deploys_the_image_version_the_api_service_reports():
    # Four files name this version and are bumped together on a runtime
    # release: the stack's image tag (what the VM pulls), the Dockerfile's
    # SERVICE_VERSION (GET /version's answer), the package __version__ and
    # the package's own pyproject.toml -- the dist metadata installed inside
    # the image, which nothing else here reads.
    import re
    import tomllib
    from pathlib import Path

    from omelet_api import __version__ as package_version

    root = Path(__file__).resolve().parent.parent
    stack = re.search(r"\$\{OMELET_API_IMAGE:-[^}]+:([^}:]+)\}",
                      (root / "runtime" / "stack.yml").read_text())
    dockerfile = re.search(r"^ARG SERVICE_VERSION=(\S+)",
                           (root / "runtime" / "omelet_api" / "Dockerfile").read_text(), re.M)
    pyproject = tomllib.loads(
        (root / "runtime" / "omelet_api" / "pyproject.toml").read_text())
    assert stack, "stack.yml must default OMELET_API_IMAGE with a tag"
    assert dockerfile, "the Dockerfile must default SERVICE_VERSION"
    pyproject_version = pyproject["project"]["version"]
    assert stack[1] == dockerfile[1] == pyproject_version == package_version


def test_the_debug_image_is_built_on_the_current_release_not_a_stale_one():
    # Dockerfile.debug's default SERVICE_IMAGE names a *published* tag to build
    # FROM, so an unbumped default silently pins debugging to an old release --
    # and since Docker never validates USER at build time, a base image built
    # before some later rename (a package name, a Linux account) fails only at
    # `docker run`, not at build. Held to the same version as the release
    # itself so this can only drift on purpose.
    import re
    from pathlib import Path

    from omelet_api import __version__ as package_version

    root = Path(__file__).resolve().parent.parent
    debug = re.search(
        r"^ARG SERVICE_IMAGE=ghcr\.io/\S+/omelet-api:(\S+)",
        (root / "runtime" / "omelet_api" / "Dockerfile.debug").read_text(), re.M)
    assert debug, "Dockerfile.debug must default SERVICE_IMAGE to a tagged omelet-api image"
    assert debug[1] == package_version


def test_the_host_speaks_the_api_the_api_service_serves():
    assert api_constants.API_VERSION in host_constants.SUPPORTED_API


def test_the_guest_cli_holds_the_same_values_as_the_host_and_the_api_service():
    # The guest CLI is copied into the VM on its own and can import neither
    # side, so its copies of the shared names are held equal here.
    from tests.runtime.cli.loader import load

    guest = _public(load())
    for side, other in (("api", _public(api_constants)),
                        ("host", _public(host_constants))):
        shared = sorted(set(guest) & set(other))
        assert shared, f"the guest CLI shares no names with the {side}"
        diverged = {name: (guest[name], other[name])
                    for name in shared if guest[name] != other[name]}
        assert not diverged, f"guest/{side} constants diverged: {diverged}"


def test_the_web_page_speaks_the_api_the_api_service_serves():
    # The page is a separate image, but ships under the same runtime tag; a
    # page that no longer lists the api service's api number shows "needs an
    # update" on every VM that installed the matching pair.
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "runtime" / "web"
              / "apps" / "console" / "src" / "api" / "version.ts").read_text()
    match = re.search(r"SUPPORTED_API[^=]*=\s*\[([^\]]*)\]", source)
    assert match, "version.ts must declare SUPPORTED_API as an array literal"
    supported = {int(n) for n in re.findall(r"\d+", match[1])}
    assert api_constants.API_VERSION in supported


def test_the_web_image_ships_with_the_api_service_it_was_built_against():
    # The page and the api service are released as a pair under one runtime tag.
    import re
    from pathlib import Path

    from omelet_api import __version__ as package_version

    stack = (Path(__file__).resolve().parent.parent / "runtime" / "stack.yml").read_text()
    web = re.search(r"\$\{OMELET_WEB_IMAGE:-[^}]+:([^}:]+)\}", stack)
    assert web, "stack.yml must default OMELET_WEB_IMAGE with a tag"
    assert web[1] == package_version


def test_the_ssh_port_the_console_shows_is_the_one_lima_is_asked_for():
    import yaml
    from pathlib import Path
    declared = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "host" / "providers" / "omelet.yaml").read_text())
    assert api_constants.LIMA_SSH_PORT == declared["ssh"]["localPort"]


def test_the_release_declares_the_api_number_the_service_speaks():
    # get.sh picks a release by this file before installing it; a release
    # declaring the wrong number installs on a host that cannot drive it.
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    release = json.loads((root / "runtime" / "release.json").read_text())
    assert release == {"api": api_constants.API_VERSION}
