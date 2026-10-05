"""What the frozen builds are allowed to contain.

The API ships as a Docker image the VM pulls and the rest of the runtime is
fetched by the VM itself; neither is ever frozen into the host binary. Nothing
enforced that except the import-boundary test, which says nothing about
`datas` -- and `datas` is how two host-side compose files came to sit under
what was then `agent/` and get bundled from there.

Every spec is checked, not just the one whose platform someone is on: a mac
build that bundles the wrong thing is invisible from Windows and the other way
round.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPECS = sorted((ROOT / "packaging").rglob("*.spec"))


def _datas(spec: Path) -> list[tuple[str, str]]:
    body = re.search(r"(?:datas=|DATAS = )\[(.*?)\]", spec.read_text(), re.S)
    assert body, f"no datas list in {spec}"
    quoted = re.findall(r'"([^"]+)"', body[1])
    assert len(quoted) % 2 == 0, f"datas entries are not (source, dest) pairs: {quoted}"
    return list(zip(quoted[::2], quoted[1::2]))


def test_there_is_a_spec_to_check():
    # Anchored to the repo root, so a rename that empties this list fails here
    # rather than passing every test below vacuously.
    assert SPECS, f"no PyInstaller specs under {ROOT / 'packaging'}"


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.parent.name)
def test_the_frozen_binary_bundles_nothing_from_runtime(spec):
    entries = _datas(spec)
    assert entries, "the spec bundles no assets at all -- this test guards nothing"
    offenders = [(src, dest) for src, dest in entries
                 if "runtime/" in src or dest.startswith("runtime")]
    assert not offenders, (
        f"the frozen host binary bundles VM-side files: {offenders}. The VM "
        "pulls the API image and fetches the rest of the runtime itself; a "
        "copy in the host would need a desktop release to change.")


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.parent.name)
def test_every_bundled_source_exists_and_lands_where_its_reader_looks(spec):
    from host.core.install import VERIFY_TEMPLATE

    entries = _datas(spec)
    for source, _dest in entries:
        resolved = (spec.parent / source).resolve()
        # A datas source may name a whole directory (PyInstaller copies it
        # recursively, e.g. host/desktop/ui). An empty one would ship an app
        # with no assets, so a directory has to actually contain files.
        assert resolved.exists(), f"{source} does not exist"
        if resolved.is_dir():
            assert any(p.is_file() for p in resolved.rglob("*")), \
                f"{source} is an empty directory"

    dests = {dest for _src, dest in entries}
    expected = {VERIFY_TEMPLATE.relative_to(ROOT).as_posix()}
    missing = expected - dests
    assert not missing, f"assets the host reads at runtime are not bundled: {missing}"


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.parent.name)
def test_the_build_stays_one_dir(spec):
    # One-file unpacks to a temp directory on every launch, which is both slower
    # and the shape antivirus heuristics dislike most; on macOS it also clashes
    # with the .app bundle, which cannot be a single file. The installer is
    # already the single artifact users download.
    text = spec.read_text()
    assert "COLLECT(" in text, "one-dir builds go through COLLECT"
    assert "exclude_binaries=True" in text


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.parent.name)
def test_two_executables_never_differ_only_in_case(spec):
    # Both executables share one directory, and a Mac filesystem is
    # case-insensitive by default: naming them `Eggie` and `eggie` built one
    # file, silently, and the app launched the CLI instead of the setup window.
    names = re.findall(r'EXE\(.*?name="([^"]+)"', spec.read_text(), re.S)
    assert names, f"no EXE names found in {spec}"
    lowered = [n.lower() for n in names]
    assert len(set(lowered)) == len(lowered), (
        f"{spec.parent.name}: executable names collide on a case-insensitive "
        f"filesystem: {names}")
