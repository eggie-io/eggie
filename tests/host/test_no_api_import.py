import ast
from pathlib import Path


def _imported_modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.lineno, node.module or ""


# Anchored to this file, never to the working directory: a cwd-relative
# Path("host") scans nothing and passes vacuously when pytest runs elsewhere.
HOST = Path(__file__).resolve().parents[2] / "host"


def test_host_never_imports_from_the_api():
    # The phase's whole point: the host ships as a frozen binary and reaches
    # the API over HTTP. An import of API code would drag FastAPI, pyyaml
    # and the guest's own logic into that binary, and would go on working in
    # this repo long after the two are deployed apart.
    offenders = []
    scanned = sorted(HOST.rglob("*.py"))
    assert scanned, f"scanned nothing under {HOST}"
    for py in scanned:
        for lineno, module in _imported_modules(ast.parse(py.read_text())):
            if module == "eggie_api" or module.startswith("eggie_api."):
                offenders.append(f"{py}:{lineno} imports {module}")
    assert not offenders, f"host/ imported API code: {offenders}"
