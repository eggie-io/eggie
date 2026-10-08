import ast
import subprocess
import sys
from pathlib import Path

import yaml

from tests.runtime.cli.loader import CLI_ROOT, GUEST_CLI, load

STACK = Path(__file__).resolve().parents[3] / "runtime" / "stack.yml"
# The entry point install.sh hands zipapp; the install test pins the same text.
ENTRY = "eggie_cli.cli:run"


def test_the_guest_cli_imports_only_the_standard_library():
    # It is built into a VM on its own: an import of host/, eggie_api/ or a
    # third-party package works in this checkout and fails only in the guest.
    files = sorted(GUEST_CLI.rglob("*.py"))
    assert len(files) > 1, f"scanned {len(files)} files under {GUEST_CLI}"
    imported = set()
    for py in files:
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
    assert imported, f"scanned no imports under {GUEST_CLI}"
    outside = sorted(imported - set(sys.stdlib_module_names) - {"eggie_cli"})
    assert not outside, f"the guest CLI imports non-stdlib modules: {outside}"


def test_the_cli_source_root_holds_only_the_package():
    # runtime/cli is the zipapp root, first on sys.path inside the executable:
    # a stray file there ships, and a name like json/ would shadow the stdlib.
    entries = sorted(p.name for p in CLI_ROOT.iterdir() if p.name != "__pycache__")
    assert entries == ["eggie_cli"], entries
    assert "eggie_cli" not in sys.stdlib_module_names


def _built_executable(tmp_path):
    # Built with the same zipapp invocation install.sh uses, flags included.
    target = tmp_path / "eggie"
    subprocess.run([sys.executable, "-m", "zipapp", str(CLI_ROOT),
                    "-m", ENTRY, "-p", sys.executable, "-o", str(target)],
                   check=True, timeout=30)
    return target


def _run_built(target, *argv):
    return subprocess.run([sys.executable, str(target), *argv],
                          capture_output=True, text=True, timeout=30)


def test_the_built_executable_starts(tmp_path):
    # The package passes its tests on sys.path and can still fail inside the
    # zip install.sh builds: a barrel import that rebinds the entry module's
    # name, say.
    run = _run_built(_built_executable(tmp_path), "--help")
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith("usage: eggie")


def test_the_built_executable_reports_a_handled_error_with_exit_1(tmp_path):
    # zipapp's own __main__ calls the entry point and drops its return value;
    # coding agents judge `eggie up` by that code. This refusal needs no token
    # and no network, so it runs anywhere.
    run = _run_built(_built_executable(tmp_path), "secret", "set", "A=b")
    assert run.returncode == 1, (run.returncode, run.stdout, run.stderr)
    assert run.stderr.startswith("The value can't go on the command line")


def test_start_stack_is_built_from_the_declared_stack_path():
    # A second literal here would let the two drift the way START_STACK's
    # hardcoded path used to risk against GUEST_ROOT/GUEST_STACK elsewhere.
    cli = load()
    assert cli.GUEST_STACK in cli.START_STACK


def test_restart_api_recreates_the_compose_service_stack_yml_defines():
    # RESTART_API is the one guidance printed for a stale token
    # (unauthorized / api_unconfigured); if the service key it recreates
    # drifts from stack.yml's own key, compose answers "no such service" and
    # the one documented recovery is a dead end. Derived from stack.yml's
    # image rather than hardcoding "api" here too, so a rename on either side
    # alone fails this test instead of both sides silently agreeing by luck.
    services = yaml.safe_load(STACK.read_text())["services"]
    (api_key,) = [name for name, svc in services.items()
                  if "eggie-api" in svc.get("image", "")]
    assert f"--force-recreate {api_key}" in load().RESTART_API


def test_the_guest_cli_slugs_project_names_the_way_the_api_does():
    # The API derives the project folder from the slugged id; a guest that
    # slugs differently checks one folder and registers another.
    from eggie_api.domain.project import _slug
    for name in ("Blog", "my app", "My.Repo", "--x--", "Ünïcode 2", "a__b", ""):
        assert load().project_id_for(name) == _slug(name), name
