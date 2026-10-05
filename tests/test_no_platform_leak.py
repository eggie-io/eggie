import re
from pathlib import Path

FORBIDDEN = re.compile(r"sys\.platform|platform\.system\(\)|os\.name")

# Anchored to this file, never to the working directory: a cwd-relative
# Path("host") scans nothing and passes vacuously when pytest runs elsewhere.
ROOT = Path(__file__).resolve().parents[1]

# The one place the host platform is resolved. Matched on the exact path, not
# on the word "providers" anywhere in it, so a future eggie_api/providers/ package
# cannot inherit the exemption by name.
EXEMPT = ROOT / "host" / "providers"

REMEDY = (
    "Push the difference into a VmProvider method instead: host/providers/ is "
    "the only module allowed to ask what platform it is on, and get_provider() "
    "is the only place that answers. Everything above the guest OS runs on the "
    "same Ubuntu on both platforms, so a branch anywhere else is describing a "
    "difference that does not exist."
)


def _hits(package: str) -> tuple[list[Path], list[str]]:
    scanned, offenders = [], []
    for py in sorted((ROOT / package).rglob("*.py")):
        if EXEMPT in py.parents:
            continue
        scanned.append(py)
        for lineno, line in enumerate(py.read_text().splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{py.relative_to(ROOT)}:{lineno}: {line.strip()}")
    return scanned, offenders


def test_platform_branching_only_in_host_providers():
    scanned, offenders = _hits("host")
    assert scanned, f"scanned nothing under {ROOT / 'host'}"
    assert not offenders, (
        "platform branching leaked outside host/providers/:\n  "
        + "\n  ".join(offenders) + f"\n\n{REMEDY}")


def test_the_api_never_asks_what_platform_it_is_on():
    # Not even an exempted one. The API runs on Linux in every deployment
    # target it will ever have -- the VM today, a cloud container next -- so a
    # platform branch there is a bug by construction, not a portability
    # measure. It also has no host to describe: it cannot see one.
    scanned, offenders = _hits("runtime/eggie_api")
    assert scanned, f"scanned nothing under {ROOT / 'runtime' / 'eggie_api'}"
    assert not offenders, (
        "the API asked what platform it is on:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe API is Linux-only by construction. Delete the branch; if "
          "the difference is really about the user's machine, it belongs to a "
          "host provider, which the API cannot and must not reach.")
