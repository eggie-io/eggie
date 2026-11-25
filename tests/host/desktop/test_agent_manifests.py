"""The agent guides that ship inside the app: what the loader accepts and
what render_steps hands the page."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from host.desktop import agents

UI = Path(__file__).resolve().parents[3] / "host" / "desktop" / "ui"


def test_every_manifest_parses_and_names_existing_media():
    manifests = agents.load(UI)
    assert [m.id for m in manifests] == ["claude-code", "codex", "cursor"]
    for m in manifests:
        assert (UI / m.icon).is_file(), m.icon
        for os_name in agents.OSES:
            for kind in agents.KINDS:
                for step in m.steps[os_name][kind]:
                    for path in step.media:
                        assert (UI / path).is_file(), path


def test_no_media_ships_that_no_step_shows():
    # The whole ui/ folder is frozen into both installers.
    shown = {path for m in agents.load(UI) for os_name in agents.OSES
             for kind in agents.KINDS for step in m.steps[os_name][kind] for path in step.media}
    shipped = {p.relative_to(UI).as_posix() for p in (UI / "agents").glob("*/media/*")}
    assert shipped and shipped == shown


def _fixture(tmp_path, **step):
    """A one-agent ui dir whose every step list holds `step`."""
    agent = tmp_path / "agents" / "demo"
    (agent / "media").mkdir(parents=True)
    (agent / "icon.webp").write_bytes(b"x")
    (agent / "media" / "a.png").write_bytes(b"x")
    one = {"title": "Open it", "body": "<p>Click <b>Go</b>.</p>", "alt": "Screenshot",
           "media": ["media/a.png"], **step}
    manifest = {"name": "Demo", "icon": "icon.webp", "get_url": "https://example.com/get",
                "connect": None,
                "steps": {os_name: {kind: [one] for kind in agents.KINDS}
                          for os_name in agents.OSES}}
    (agent / "agent.json").write_text(json.dumps(manifest))
    (tmp_path / "agents" / "index.json").write_text('{"agents": ["demo"]}')
    return tmp_path


def test_a_plain_step_loads(tmp_path):
    [m] = agents.load(_fixture(tmp_path))
    assert m.steps["windows"]["cloud"][0].media == ("agents/demo/media/a.png",)


@pytest.mark.parametrize("body", [
    '<a href="https://x">here</a>',          # a tag outside the list
    '<b class="x">Go</b>',                   # any attribute
    "<p>Click <b>Go</p>",                    # unclosed inside
    "<b>Go",                                  # unclosed at the end
    "Go</b>",                                 # a close with no open
    "Run {user}@{host}",                      # a template the app never fills
    "<script>alert(1)</script>",
])
def test_bodies_use_only_allowed_tags_and_templates(tmp_path, body):
    with pytest.raises(ValueError):
        agents.load(_fixture(tmp_path, body=body))


def test_allowed_markup_and_templates_load(tmp_path):
    body = "<ol><li>Pick <code>{ssh_host}</code>,<br>then <kbd>Enter</kbd> on {kitchen}.</li></ol>"
    agents.load(_fixture(tmp_path, body=body))


@pytest.mark.parametrize("step", [
    {"media": ["media/missing.png"]},
    {"media": ["../../../secret.png"]},
    {"alt": "{user}"},
])
def test_a_step_naming_a_missing_or_outside_file_or_bad_template_is_refused(tmp_path, step):
    with pytest.raises(ValueError):
        agents.load(_fixture(tmp_path, **step))


def test_a_manifest_missing_a_platform_is_refused(tmp_path):
    ui = _fixture(tmp_path)
    path = ui / "agents" / "demo" / "agent.json"
    data = json.loads(path.read_text())
    del data["steps"]["windows"]["cloud"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        agents.load(ui)


def test_an_unknown_connect_kind_is_refused(tmp_path):
    ui = _fixture(tmp_path)
    path = ui / "agents" / "demo" / "agent.json"
    data = json.loads(path.read_text())
    data["connect"] = "cursor"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        agents.load(ui)


def test_cloud_steps_name_the_kitchen_ssh_host():
    for m in agents.load(UI):
        for os_name in agents.OSES:
            steps = agents.render_steps(m, os_name, "cloud", "eggie-ihor", "Ihor’s cloud")
            assert any("eggie-ihor" in s["body"] for s in steps), (m.id, os_name)
            assert all("{" not in s["body"] + s["title"] + s["alt"] for s in steps)


def test_the_kitchen_name_is_escaped_in_bodies_and_never_expanded(tmp_path):
    body = "<p>Pick {kitchen} on {ssh_host}.</p>"
    [m] = agents.load(_fixture(tmp_path, body=body, title="Open {kitchen}"))
    [step] = agents.render_steps(m, "mac", "cloud", "eggie-x", "<b>{ssh_host}</b> & co")
    assert step["body"] == "<p>Pick &lt;b&gt;{ssh_host}&lt;/b&gt; &amp; co on eggie-x.</p>"
    # Titles are drawn as text, so they keep the name as typed.
    assert step["title"] == "Open <b>{ssh_host}</b> & co"


def test_render_steps_refuses_an_unknown_platform():
    m = agents.load(UI)[0]
    with pytest.raises(ValueError):
        agents.render_steps(m, "linux", "local", "eggie", "This computer")


def _with_confirm(tmp_path, confirm):
    root = _fixture(tmp_path)
    path = root / "agents" / "demo" / "agent.json"
    data = json.loads(path.read_text())
    data["confirm"] = confirm
    path.write_text(json.dumps(data))
    return root


def test_a_confirm_is_kept_per_os():
    [codex] = [m for m in agents.load(UI) if m.id == "codex"]
    assert set(codex.confirm) == {"windows"}
    assert codex.confirm["windows"].button == "Make eggie-vm the default"


@pytest.mark.parametrize("confirm", [
    {"linux": {"title": "T", "body": "<p>B</p>", "button": "Go"}},
    {"windows": {"title": "T", "body": '<a href="x">B</a>', "button": "Go"}},
    {"windows": {"title": "T", "body": "<p>B {kitchen}</p>", "button": "Go"}},
    {"windows": {"title": "T", "body": "<p>B</p>"}},
])
def test_a_confirm_the_page_could_not_draw_safely_fails_the_load(tmp_path, confirm):
    with pytest.raises(ValueError, match="agents/demo"):
        agents.load(_with_confirm(tmp_path, confirm))
