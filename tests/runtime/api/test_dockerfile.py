from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[3] / "runtime" / "eggie_api" / "Dockerfile"


def _text() -> str:
    return DOCKERFILE.read_text()


def test_dockerfile_never_pins_latest():
    # A moving tag defeats the whole point of pinning the CLI and the API
    # version -- a rebuild months later would silently pick up a new major.
    assert ":latest" not in _text()


def test_dockerfile_puts_the_docker_cli_at_the_absolute_path_lifecycle_expects():
    # core/lifecycle.py calls "/usr/bin/docker" literally; landing the binary
    # anywhere else makes every guest exec fail with "No such file".
    assert "/usr/local/bin/docker /usr/bin/docker" in _text()


def test_dockerfile_never_touches_opt_eggie():
    # /opt/eggie is a bind mount holding user projects and the state db. A
    # COPY, VOLUME or WORKDIR targeting it would let an image pull mask or
    # destroy it. Checking only the instructions that matter, rather than the
    # raw file text, leaves room for an explanatory comment that mentions the
    # path -- exactly the kind of comment this mount deserves.
    assert "VOLUME" not in _text()
    for line in _text().splitlines():
        instruction = line.strip()
        if instruction.startswith(("COPY", "WORKDIR")):
            assert "/opt/eggie" not in instruction, line


def test_dockerfile_installs_git_for_the_github_clone_job():
    assert "apt-get install" in _text() and " git" in _text()


def test_dockerfile_starts_the_package_entrypoint():
    assert '"-m", "eggie_api"]' in _text()
