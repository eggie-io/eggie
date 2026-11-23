import pytest
from typer.testing import CliRunner

import host.cli as cli
from host.core.connect.flow import Line

runner = CliRunner()


class FakeConnector:
    def __init__(self, lines):
        self.lines = lines
        self.calls = []

    def connect(self, agents, instances, local):
        self.calls.append(("connect", agents, instances, local))
        return self.lines

    def remove(self, agents, instances, local):
        self.calls.append(("remove", agents, instances, local))
        return self.lines


def run(monkeypatch, fake, argv):
    monkeypatch.setattr(cli, "_connector_factory", lambda say: fake)
    return runner.invoke(cli.app, ["connect", *argv])


def test_connect_exits_1_when_any_line_fails(monkeypatch):
    fake = FakeConnector([Line("Shop", "Claude Code", True, "connected"),
                          Line("Shop", "Codex", False, "sign-in did not finish")])
    result = run(monkeypatch, fake, [])
    assert result.exit_code == 1
    assert "✓ Shop · Claude Code: connected" in result.output
    assert "✗ Shop · Codex: sign-in did not finish" in result.output


@pytest.mark.parametrize("argv, call", [
    ([], ("connect", {"claude", "codex"}, None, True)),
    (["--local"], ("connect", {"claude", "codex"}, [], True)),
    (["--kitchen", "a", "--kitchen", "b", "--codex"], ("connect", {"codex"}, ["a", "b"], False)),
    (["--kitchen", "a", "--local", "--claude"], ("connect", {"claude"}, ["a"], True)),
    (["--remove"], ("remove", {"claude", "codex"}, None, True)),
])
def test_selectors_reach_the_connector(monkeypatch, argv, call):
    fake = FakeConnector([Line("Shop", None, True, "ok")])
    result = run(monkeypatch, fake, argv)
    assert result.exit_code == 0, result.output
    assert fake.calls == [call]


def test_a_skipped_kitchen_is_neither_tick_nor_cross(monkeypatch):
    fake = FakeConnector([Line("Cold", None, True, "Skipped: stopped", neutral=True)])
    result = run(monkeypatch, fake, [])
    assert result.exit_code == 0
    assert "\u2013 Cold: Skipped: stopped" in result.output
