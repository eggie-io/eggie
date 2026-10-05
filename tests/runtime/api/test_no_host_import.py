import ast
from pathlib import Path


def _imported_modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.lineno, node.module or ""


API = Path(__file__).resolve().parents[3] / "runtime" / "eggie_api"


def test_the_api_never_imports_from_the_host():
    # The API ships as a Docker image built from runtime/eggie_api/ alone; an
    # import of host code would only fail once the image runs.
    offenders = []
    scanned = sorted(API.rglob("*.py"))
    assert scanned, f"scanned nothing under {API}"
    for py in scanned:
        for lineno, module in _imported_modules(ast.parse(py.read_text())):
            if module == "host" or module.startswith("host."):
                offenders.append(f"{py}:{lineno} imports {module}")
    assert not offenders, f"the API imported host code: {offenders}"
