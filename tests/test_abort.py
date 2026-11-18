"""Ctrl-C / EOF at a prompt prints a styled abort notice on its own line."""

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

REPO_KEYS = (
    "esphome_path",
    "esphome_io_path",
    "esphome_hassio_path",
    "esphome_issues_path",
    "esphome_feature_requests_path",
)


@pytest.fixture(autouse=True)
def restore_modules() -> Iterator[None]:
    """Put sys.modules back so the fresh import of commands does not leak."""
    saved = {k: v for k, v in sys.modules.items() if k.startswith("esphomerelease")}
    yield
    added = [k for k in sys.modules if k.startswith("esphomerelease") and k not in saved]
    for name in [k for k in sys.modules if k.startswith("esphomerelease")]:
        del sys.modules[name]
    sys.modules.update(saved)
    # A leftover package attribute would be handed out by ``from pkg import mod``.
    for name in added:
        parent, _, child = name.rpartition(".")
        if parent in saved and hasattr(saved[parent], child):
            delattr(saved[parent], child)
    for name, mod in saved.items():
        parent, _, child = name.rpartition(".")
        if parent:
            setattr(sys.modules[parent], child, mod)


def _evict(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [k for k in sys.modules if k.startswith("esphomerelease.")]:
        if name != "esphomerelease.exceptions":
            monkeypatch.delitem(sys.modules, name)
            monkeypatch.delattr(sys.modules["esphomerelease"], name.split(".")[1], raising=False)


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> click.Group:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = {"github_token": "x", "step": False}
    config.update({key: str(repo_dir) for key in REPO_KEYS})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(config))
    _evict(monkeypatch)
    from esphomerelease import commands

    @click.command()
    def prompt_cmd() -> None:
        def eof(prompt: str) -> str:
            raise EOFError

        # Real click.prompt; the terminal read (which CliRunner has swapped for a fake
        # that loops forever on empty input) raises EOF as Ctrl-D at a tty would.
        monkeypatch.setattr(click.termui, "visible_prompt_func", eof)
        click.prompt("Please enter base")

    @click.command()
    def interrupt_cmd() -> None:
        raise KeyboardInterrupt

    @click.command()
    def ok_cmd() -> None:
        click.echo("fine")

    monkeypatch.setitem(commands.cli.commands, "prompt-cmd", prompt_cmd)
    monkeypatch.setitem(commands.cli.commands, "interrupt-cmd", interrupt_cmd)
    monkeypatch.setitem(commands.cli.commands, "ok-cmd", ok_cmd)
    return commands.cli


def test_abort_at_prompt(cli: click.Group) -> None:
    result = CliRunner(mix_stderr=False).invoke(cli, ["prompt-cmd"])
    assert result.exit_code == 1
    assert result.stderr == "\nAborted.\n"
    assert "Aborted!" not in result.stderr


def test_keyboard_interrupt(cli: click.Group) -> None:
    result = CliRunner(mix_stderr=False).invoke(cli, ["interrupt-cmd"])
    assert result.exit_code == 1
    assert result.stderr == "\nAborted.\n"
    assert "Aborted!" not in result.stderr


def test_normal_command_unaffected(cli: click.Group) -> None:
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, ["ok-cmd"])
    assert (result.exit_code, result.stdout, result.stderr) == (0, "fine\n", "")
    assert runner.invoke(cli, ["--help"]).exit_code == 0
