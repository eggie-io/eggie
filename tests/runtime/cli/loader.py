"""The guest CLI is a standalone script copied into the VM, never a module of
host/, so tests load it by path the way the VM runs it."""
import importlib.util
import sys
from pathlib import Path

GUEST_CLI = (Path(__file__).resolve().parents[3]
             / "runtime" / "cli" / "eggie.py")
_NAME = "eggie_guest_cli"


def load():
    if _NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(_NAME, GUEST_CLI)
        module = importlib.util.module_from_spec(spec)
        # Registered before exec: dataclasses resolve their module through
        # sys.modules while the class body runs.
        sys.modules[_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_NAME]
