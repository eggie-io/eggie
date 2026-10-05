from typer.testing import CliRunner
from host.cli import app
from host.core import constants

runner = CliRunner()


def test_version_command_runs():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"eggie {constants.APP_VERSION}" in result.stdout
