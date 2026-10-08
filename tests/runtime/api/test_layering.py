"""The one dependency edge inside eggie_api: rest -> services -> domain | infra."""
import ast
from pathlib import Path

API = Path(__file__).resolve().parents[3] / "runtime" / "eggie_api"
IO_LIBS = {"sqlite3", "subprocess", "urllib", "fastapi", "starlette", "pydantic"}
WEB_LIBS = {"fastapi", "starlette", "pydantic"}


def _imports(py: Path):
    tree = ast.parse(py.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative: resolve against the file's package so `..infra.db`
                # from services/x.py reads as `eggie_api.infra.db`.
                parts = py.relative_to(API).with_suffix("").parts[:-node.level]
                yield "eggie_api." + ".".join((*parts, node.module or "")).strip(".")
            else:
                yield node.module or ""


def _layer(py: Path) -> str:
    rel = py.relative_to(API).parts
    return rel[0] if len(rel) > 1 else rel[0].removesuffix(".py")


def _target(name: str) -> str:
    """The layer (`infra`, `services`, …) for a package import, the top-level
    library name for anything else."""
    parts = name.split(".")
    if parts[0] == "eggie_api":
        return parts[1] if len(parts) > 1 else ""
    return parts[0]


def test_layers_only_depend_downward():
    forbidden = {
        "domain": {"infra", "services", "rest", *IO_LIBS},
        "infra": {"services", "rest", *WEB_LIBS},
        "services": {"rest", *WEB_LIBS},
        "config": {"services", "rest"},
        "constants": {"domain", "infra", "services", "rest"},
        "errors": {"domain", "infra", "services", "rest"},
    }
    offenders = []
    files = [p for p in API.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(files) >= 40, f"scanned only {len(files)} files under {API}"
    for py in files:
        layer = _layer(py)
        for name in _imports(py):
            if _target(name) in forbidden.get(layer, set()):
                offenders.append(f"{py.relative_to(API)} imports {name}")
    assert not offenders, "\n".join(offenders)


def test_only_rest_wiring_and_main_import_services():
    allowed = {"rest", "wiring", "__main__", "services"}
    offenders = [f"{py.relative_to(API)} imports {name}"
                 for py in API.rglob("*.py") if "__pycache__" not in py.parts
                 for name in _imports(py)
                 if _target(name) == "services" and _layer(py) not in allowed]
    assert not offenders, "\n".join(offenders)


def test_no_package_directory_shadows_the_standard_library():
    # The image copies the package into its working directory, which is first
    # on sys.path for `python -m` and the healthcheck's `python -c`; a top-level
    # `http/` once made `import http.client` fail inside the container.
    import sys
    tops = {p.name for p in API.iterdir() if p.is_dir() and p.name != "__pycache__"}
    tops |= {p.stem for p in API.glob("*.py") if p.stem != "__init__"}
    clashes = sorted(tops & set(sys.stdlib_module_names))
    assert not clashes, f"these shadow the stdlib from the image's working dir: {clashes}"
