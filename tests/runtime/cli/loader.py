"""The guest CLI is a stdlib-only package built into one executable for the
VM, never a module of host/; tests put its source root on sys.path the way
the zipapp puts the archive root there."""
import importlib
import sys
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[3] / "runtime" / "cli"
GUEST_CLI = CLI_ROOT / "eggie_cli"


def load():
    if str(CLI_ROOT) not in sys.path:
        sys.path.insert(0, str(CLI_ROOT))
    return importlib.import_module("eggie_cli")
