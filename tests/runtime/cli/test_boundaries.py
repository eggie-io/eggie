import ast
import subprocess
import sys
import zipapp
from pathlib import Path

import yaml

from tests.runtime.cli.loader import CLI_ROOT, GUEST_CLI, load

STACK = Path(__file__).resolve().parents[3] / "runtime" / "stack.yml"


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


def test_the_built_executable_starts(tmp_path):
    # The package passes its tests on sys.path and can still fail inside the
    # zip install.sh builds: a barrel import that rebinds the entry module's
    # name, say. Build it the way install.sh does and run it.
    target = tmp_path / "eggie"
    zipapp.create_archive(CLI_ROOT, target, interpreter=sys.executable,
                          main="eggie_cli.cli:main")
    run = subprocess.run([sys.executable, str(target), "--help"],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith("usage: eggie")


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
