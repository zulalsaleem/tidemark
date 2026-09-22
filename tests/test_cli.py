"""Smoke test: the CLI is wired up and runnable end to end."""

from typer.testing import CliRunner

from tidemark import __version__
from tidemark.cli import app

runner = CliRunner()


def test_help_exits_cleanly() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Tidemark" in result.stdout


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_run_command_not_yet_implemented() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code != 0
    assert isinstance(result.exception, NotImplementedError)
