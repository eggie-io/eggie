"""The runtime release workflow's one ordering rule: images before the tag."""
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release-runtime.yml"


def _steps() -> list[str]:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    return [step.get("run", "") for job in jobs.values() for step in job["steps"]]


def test_the_tag_is_pushed_only_after_the_images_are():
    # VMs install a runtime-v* tag the moment they see it; a tag whose images
    # are not on ghcr yet fails every update that starts in between.
    runs = _steps()
    build = next(i for i, run in enumerate(runs) if "packaging/images/build.sh --push" in run)
    tag = next(i for i, run in enumerate(runs) if 'git push origin "runtime-v' in run)
    assert build < tag


def test_the_images_are_built_with_the_released_version():
    assert any('build.sh --push --version "$VERSION"' in run for run in _steps())
