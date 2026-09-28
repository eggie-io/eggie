import re
import subprocess
from pathlib import Path

import yaml

from host.core import constants

# Anchored to this file, never to the working directory: a cwd-relative path
# scans nothing and fails (or passes) for the wrong reason when pytest runs
# elsewhere. Same rule as tests/test_no_platform_leak.py and the two
# import-boundary tests.
ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "runtime" / "install" / "install.sh"
# install.sh still writes this path directly; it stands in for a host
# constant that no longer exists.
STACK = f"{constants.GUEST_ROOT}/stack.yml"
STACK_YML = ROOT / "runtime" / "stack.yml"


def test_install_is_valid_bash():
    # `bash -n` parses without executing; catches syntax errors.
    result = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_install_pins_docker_official_repo_not_docker_io():
    text = INSTALL.read_text()
    assert "download.docker.com" in text
    assert "docker.io" not in text


def test_install_writes_the_marker_path_the_host_checks():
    # The host treats a missing marker after a zero exit as a failed install;
    # two spellings would fail every install that actually worked.
    assert f"> {constants.RUNTIME_MARKER}" in INSTALL.read_text()


def test_install_guards_on_the_package_not_the_docker_binary():
    # Docker Desktop's WSL integration puts its own docker CLI on PATH. Guarding
    # on `command -v docker` skipped the install and then failed at
    # `systemctl enable` with "Unit file docker.service does not exist".
    code = [l for l in INSTALL.read_text().splitlines()
            if not l.lstrip().startswith("#")]
    assert any("dpkg -s docker-ce" in l for l in code)
    assert not any("command -v docker" in l for l in code)


# A word is being *run* only at the start of a line or right after a pipe,
# `&&`/`||`, `;`, `(`, `!`, or then/else/do. Anchoring on the line start alone
# missed `... || docker network create`, which is the same bug one operator in.
_BARE_DOCKER = re.compile(r"(?:^|[|&;(!]|\b(?:then|else|do)\s)\s*docker\b")


def test_install_invokes_docker_by_absolute_path():
    # A bare `docker` would reach Docker Desktop's CLI when its WSL integration
    # is on, sending this VM's containers to Desktop's engine instead.
    for line in _commands():
        assert not _BARE_DOCKER.search(line), f"bare docker invocation: {line}"


def _commands() -> list[str]:
    """Executable lines only -- a comment mentioning `docker run traefik` must
    not satisfy or trip the assertions below."""
    return [l.strip() for l in INSTALL.read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def _index_of(needle: str) -> int:
    for i, line in enumerate(_commands()):
        if needle in line:
            return i
    raise AssertionError(f"install.sh has no line containing {needle!r}")


def test_install_brings_the_stack_up_with_compose_not_an_inline_container():
    # Traefik and the API are one compose stack now; an inline `docker run`
    # would start a Traefik outside it that compose can never upgrade or stop.
    commands = _commands()
    assert any(f"compose -f {STACK}" in l and " up -d" in l
               for l in commands)
    assert not any("docker run" in l for l in commands), "the inline traefik container is gone"
    assert not any("docker rm -f traefik" in l for l in commands)
    assert not any("traefik.yml" in l for l in commands)


def test_a_missing_architecture_is_not_reported_as_a_network_problem():
    # The failure an Apple Silicon user hits first: the API image is published
    # for amd64 only, the arm64 VM cannot pull it, and the daemon says "no
    # matching manifest". Reporting that as "the registry was unreachable.
    # Check the network connection or proxy" points at the one part of the
    # system that is working.
    text = INSTALL.read_text()
    assert "no matching manifest" in text and "no match for platform" in text, \
        "both spellings the daemon uses must be matched"
    arch_branch = text.split("no matching manifest")[1].split("else")[0]
    assert "architecture" in arch_branch
    assert "proxy" not in arch_branch, \
        "an architecture failure must not send the user to their network settings"
    assert "uname -m" in arch_branch, "say which architecture was asked for"


def test_the_pull_still_shows_its_progress_while_being_recorded():
    # Captured output alone would leave a multi-minute download silent; `tee`
    # is what keeps both.
    text = INSTALL.read_text()
    assert "pull 2>&1 | tee" in text


def test_install_always_pulls_before_bringing_the_stack_up():
    # Always pulling is the delivery decision: it is how an API update reaches
    # an already-bootstrapped VM. `up -d` alone would keep running a stale image.
    assert _index_of(f"compose -f {STACK} --profile tunnel pull") < _index_of(" up -d")


def test_the_stack_comes_up_without_the_tunnel_profile():
    # The API starts the tunnel client only while a public URL is on.
    ups = [l for l in _commands() if f"compose -f {STACK}" in l and " up -d" in l]
    assert ups and not any("--profile" in l for l in ups)


def test_install_makes_opt_omelet_writable_before_the_api_starts():
    # The API runs as a non-root user whose only shared credential with the VM
    # is the docker group. Root-owned 0755 here means it cannot create
    # /opt/omelet/state.db, and `restart: always` then loops it forever.
    up = _index_of(" up -d")
    assert _index_of("chgrp") < up
    assert any("docker" in l for l in _commands() if "chgrp" in l), \
        "the group the API actually belongs to"
    # g+rwX is the line that grants the access; chgrp alone leaves 0755 and the
    # API still cannot create state.db. X, not x, so files stay non-executable.
    assert _index_of("g+rwX") < up
    assert _index_of("g+s") < up, \
        "setgid, or project directories the API creates lose the group"
    assert _index_of("mkdir -p " + constants.GUEST_PROJECTS) < _index_of("chgrp")


def test_install_writes_the_marker_last():
    commands = _commands()
    marker = _index_of(f"> {constants.RUNTIME_MARKER}")
    assert marker > _index_of('"$SKILLS_CLI" add')
    assert marker >= len(commands) - 2, "nothing that can fail may run after the marker"


def test_a_failed_reinstall_does_not_leave_the_previous_marker_standing():
    # The runtime dir, stack and skills are already replaced by the time any
    # Docker step could fail; the old marker must not go on claiming success.
    commands = _commands()
    rm_marker = _index_of(f"rm -f {constants.RUNTIME_MARKER}")
    assert rm_marker < _index_of("dpkg -s docker-ce")
    write_marker = _index_of(f"> {constants.RUNTIME_MARKER}")
    assert write_marker >= len(commands) - 2, "nothing that can fail may run after the marker"


def test_install_has_no_early_exit_of_its_own():
    # get.sh decides whether to install; the old marker's early `exit 0` left
    # here would turn every repair into a silent no-op.
    assert "exit 0" not in _commands()


def test_skills_cli_is_pinned():
    assert re.search(r"^SKILLS_CLI=skills@\d+\.\d+\.\d+$", INSTALL.read_text(), re.M), \
        "an unpinned skills CLI changes the install without a release"


def test_the_skills_install_from_their_own_repository():
    # Shipping them in the tarball is what kept the skills entangled with the
    # runtime: `add` was doing the filing while the tarball did the
    # distributing. The argument must be a remote source, not a local path.
    script = (ROOT / "runtime" / "install" / "install.sh").read_text()
    assert "ihorklymchukdev/omelet-skills" in script
    assert "$RUNTIME_DIR/skills" not in script
    assert "$INSTALL_DIR/skills" not in script


def test_the_skills_source_is_overridable():
    # Pinning a ref must be a value change, not a code change.
    script = (ROOT / "runtime" / "install" / "install.sh").read_text()
    assert "${OMELET_SKILLS_SOURCE:-" in script


def test_npx_in_the_account_loop_cannot_swallow_the_account_list():
    # The loop reads accounts from stdin; a command inside it that reads stdin
    # consumes the remaining accounts, and only the first user gets skills.
    runuser = [l for l in _commands() if "runuser" in l]
    assert runuser, "skills are installed per account"
    assert all("</dev/null" in l for l in runuser)


def test_a_repair_or_a_new_token_recreates_the_api():
    # The API reads its token once at startup; `up -d` leaves it running.
    # The service key is derived from stack.yml, not hardcoded "api" here too
    # -- the same way tests/runtime/cli/test_boundaries.py derives the CLI's
    # RESTART_API -- so a rename of stack.yml's service key alone fails this
    # test instead of leaving install.sh silently recreating a service that
    # no longer exists.
    services = yaml.safe_load(STACK_YML.read_text())["services"]
    (api_key,) = [name for name, svc in services.items()
                  if "omelet-api" in svc.get("image", "")]
    commands = _commands()
    recreate = _index_of(f"--force-recreate {api_key}")
    assert recreate > _index_of(" up -d")
    condition = commands[recreate - 1]
    assert "TOKEN_CREATED" in condition and "REPAIR" in condition, condition


def test_install_generates_the_token_only_if_absent():
    # Bootstrap re-runs are normal (idempotency by design); regenerating the
    # token on every run would invalidate a credential the host is already
    # holding.
    assert "[[ ! -s " + constants.GUEST_TOKEN + " ]]" in INSTALL.read_text()


def test_install_generates_the_token_without_a_sigpipe_trap():
    # tr fed straight from /dev/urandom never terminates on its own; bounding
    # its output with a downstream `head -c` kills it with SIGPIPE the moment
    # head stops reading, and `set -o pipefail` then fails the whole script
    # for a byte count that was never wrong. Bounding /dev/urandom itself at
    # the head of the pipeline avoids the trap entirely.
    text = INSTALL.read_text()
    assert "head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \\n'" in text
    assert "tr -dc" not in text


def test_install_writes_the_token_before_the_stack_comes_up():
    commands = _commands()
    token_write = _index_of(constants.GUEST_TOKEN)
    assert token_write < _index_of(" up -d")


def test_install_writes_the_token_after_the_permissions_sweep_widens_it():
    # A mode-600 token written before `chmod -R g+rwX /opt/omelet` comes out
    # group-readable; written after, its own chmod is the last word.
    commands = _commands()
    token_write = _index_of(f"[[ ! -s {constants.GUEST_TOKEN} ]]")
    assert _index_of("g+rwX") < token_write


def test_install_reasserts_a_narrow_mode_on_the_token_after_writing_it():
    commands = _commands()
    chmod = _index_of(f"chmod 640 {constants.GUEST_TOKEN}")
    assert chmod > _index_of(f"[[ ! -s {constants.GUEST_TOKEN} ]]")
    assert chmod < _index_of(" up -d")


def test_install_creates_the_token_at_a_narrow_mode_from_the_start():
    # A plain `>` redirect creates the file under root's umask (644) before
    # any later chmod narrows it, leaving a window where it is
    # world-readable. `install -m` sets the mode at creation instead.
    commands = _commands()
    create = _index_of(f"install -m 640 /dev/null {constants.GUEST_TOKEN}")
    assert create > _index_of(f"[[ ! -s {constants.GUEST_TOKEN} ]]")
    assert create < _index_of(f"head -c 32 /dev/urandom")


def test_install_chgrps_the_token_to_docker():
    # The other half of the 640/docker permission model: without this, the
    # token's group stays whatever `install`/root's process defaults to,
    # which the API's own group membership may not be.
    commands = _commands()
    chgrp = _index_of(f"chgrp docker {constants.GUEST_TOKEN}")
    assert chgrp < _index_of(" up -d")
    assert chgrp < _index_of(f"chmod 640 {constants.GUEST_TOKEN}")


def test_install_writes_this_vms_real_docker_gid_for_the_stack():
    # stack.yml's group_add defaults to 999 and the image bakes in 999, but the
    # chgrp above uses whatever GID this VM's docker group actually has. On a VM
    # where they differ the API can write neither /opt/omelet nor the socket
    # -- the same crash-loop the chgrp exists to prevent, one step over.
    commands = _commands()
    env_line = _index_of(f"{constants.GUEST_ROOT}/.env")
    assert env_line < _index_of(" up -d"), "compose reads .env when it starts"
    assert any("OMELET_DOCKER_GID" in l for l in commands)
    assert any("getent group docker" in l for l in commands), \
        "the GID must be read from the VM, not assumed"
    assert not any(re.search(r"OMELET_DOCKER_GID=[0-9]", l) for l in commands), \
        "a literal GID is the bug this guards against"


def test_install_gets_the_github_cli_from_githubs_own_repo():
    # Ubuntu 24.04 ships no gh at all, and the third-party mirrors that carry
    # one lag releases badly. cli.github.com is the only source that is both
    # current and published for amd64 and arm64 -- the VM is arm64 under Lima.
    commands = _commands()
    assert any("cli.github.com" in l for l in commands)
    assert any(re.search(r"apt-get install -y( .*)? gh\b", l) for l in commands), \
        "gh must come from the apt repo, not a downloaded tarball"


def test_install_guards_gh_on_the_package_not_the_binary():
    # Same trap as docker-ce: a gh on PATH from somewhere else would skip the
    # install and leave the repo unconfigured for every later upgrade.
    code = _commands()
    assert any("dpkg -s gh" in l for l in code)
    assert not any("command -v gh" in l for l in code)


def test_the_github_cli_repo_is_signed_by_its_keyring():
    # An unsigned apt source lets anything that can answer for cli.github.com
    # install a root-run package into the VM.
    (source_line,) = [l for l in _commands() if "cli.github.com/packages stable" in l]
    assert "signed-by=/etc/apt/keyrings/" in source_line
    assert "trusted=yes" not in source_line
    assert "arch=$(dpkg --print-architecture)" in source_line, \
        "the VM is arm64 under Lima and amd64 under WSL2"


def test_a_github_cli_failure_is_reported_and_stops_the_install():
    # provider.exec() never raises and install.sh runs behind a bootstrap that
    # only re-checks runtime.version -- a silently skipped gh would look installed.
    text = INSTALL.read_text()
    gh_block = text.split("dpkg -s gh")[1].split("node_ok()")[0]
    assert gh_block.count("exit 1") == 2, "both the keyring and the apt failure must stop"
    assert "cli.github.com" in gh_block and ">&2" in gh_block


def test_install_removes_what_an_engine_v_install_left_behind():
    # A VM installed from an engine-v* ref keeps /opt/omelet/engine/,
    # engine.version and agent.token forever otherwise: nothing reads them
    # any more, engine.version sits beside runtime.version to mislead the
    # next person who debugs the box, and agent.token is a live 0640
    # docker-readable secret. The cleanup command spans several
    # backslash-continued lines, so join them before checking.
    text = INSTALL.read_text()
    block = text.split("rm -rf ", 1)[1].split("\nif ", 1)[0]
    cleanup = block.replace("\\\n", " ")
    assert "omelet-setup" in cleanup, "sanity check: not the intended block"
    for stale in ("/opt/omelet/engine ", "/opt/omelet/engine.version",
                  "/opt/omelet/agent.token"):
        assert stale in cleanup, f"cleanup no longer removes {stale!r}"


def test_install_reasserts_the_github_file_modes_after_the_permission_sweep():
    text = INSTALL.read_text()
    sweep = text.index("chmod -R g+rwX /opt/omelet")
    assert text.index("chmod 600 /opt/omelet/github/token") > sweep
    assert "install -d -m 2770 -o root -g docker /opt/omelet/github" in text


def test_install_enables_the_github_path_unit_and_applies_before_the_marker():
    text = INSTALL.read_text()
    assert "systemctl enable --now omelet-github.path" in text
    # The oneshot service, not the script directly: the call still blocks,
    # runs stay serialized, and the unit's environment is clean.
    apply = text.index("systemctl start omelet-github.service")
    assert apply < text.index(f"> {constants.RUNTIME_MARKER}")


def test_install_trusts_repositories_owned_by_the_api():
    assert "safe.directory '*'" in INSTALL.read_text()


def test_the_agent_instructions_never_ask_for_a_github_login():
    text = (ROOT / "runtime" / "instructions" / "omelet.md").read_text()
    text_joined = " ".join(text.split())
    assert "Connect GitHub" in text
    assert "do not run `gh auth login`" in text_joined
    assert "read the user the code it prints" not in text_joined


def test_install_records_how_agents_reach_the_vm_before_the_marker():
    text = INSTALL.read_text()
    record = text.index("/opt/omelet/connect.json")
    assert record < text.index("> /opt/omelet/runtime.version")
    assert "/proc/sys/kernel/osrelease" in text and "/mnt/lima-cidata" in text
