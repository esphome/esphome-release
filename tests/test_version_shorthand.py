"""Tests for the ``s``/``b`` VERSION shorthands of ``cut`` and ``publish``.

The shorthands are resolved against the latest release (prereleases included):
``s`` is the next stable (the cycle's final ``.0`` during a beta period, a
patch otherwise) and ``b`` is the next beta.

``commands`` imports ``.project``, which instantiates every ``Project`` at
import time and asserts each configured path is a directory. The ``commands``
fixture writes a temp ``config.json`` whose paths point at real directories so
the modules are importable, mirroring the import-safe reload pattern used
elsewhere in this repo.
"""

import importlib
import json

import pytest
from click.testing import CliRunner


@pytest.fixture
def commands(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = {
        "github_token": "x",
        "step": False,
        "esphome_path": str(repo_dir),
        "esphome_io_path": str(repo_dir),
        "esphome_hassio_path": str(repo_dir),
        "esphome_issues_path": str(repo_dir),
        "esphome_feature_requests_path": str(repo_dir),
    }
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(config))

    import esphomerelease.config as config_mod

    importlib.reload(config_mod)
    import esphomerelease.project as project_mod

    importlib.reload(project_mod)
    import esphomerelease.cutting as cutting_mod

    importlib.reload(cutting_mod)
    import esphomerelease.commands as commands_mod

    importlib.reload(commands_mod)
    return commands_mod


@pytest.fixture
def cut_calls(commands, monkeypatch):
    """Record the versions ``cut`` hands to the cutting module."""
    calls = []
    monkeypatch.setattr(
        commands.cutting, "cut_beta_release", lambda v: calls.append(("beta", v))
    )
    monkeypatch.setattr(
        commands.cutting, "cut_release", lambda v: calls.append(("stable", v))
    )
    monkeypatch.setattr(commands, "_commit_user_cache_if_changed", lambda: None)
    return calls


@pytest.fixture
def publish_calls(commands, monkeypatch):
    """Record the versions ``publish`` hands to the cutting module."""
    calls = []
    monkeypatch.setattr(
        commands.cutting,
        "publish_beta_release",
        lambda v, projects: calls.append(("beta", v, projects)),
    )
    monkeypatch.setattr(
        commands.cutting,
        "publish_release",
        lambda v, projects: calls.append(("stable", v, projects)),
    )
    return calls


def _set_latest(commands, monkeypatch, latest: str) -> None:
    from esphomerelease.model import Version

    monkeypatch.setattr(
        commands.EsphomeProject,
        "latest_release",
        lambda **kwargs: Version.parse(latest),
    )


@pytest.mark.parametrize(
    ("latest", "argument", "expected"),
    [
        # During a beta period 's' cuts the cycle's final release.
        ("2026.8.0b4", "s", "2026.8.0"),
        ("2026.8.0b4", "stable", "2026.8.0"),
        ("2026.8.0b4", "S", "2026.8.0"),
        # Outside a beta period 's' is the next patch.
        ("2026.8.0", "s", "2026.8.1"),
        ("2026.8.2", "s", "2026.8.3"),
        # During a beta period 'b' is the next beta of the same cycle.
        ("2026.8.0b4", "b", "2026.8.0b5"),
        ("2026.8.0b4", "beta", "2026.8.0b5"),
        # Outside a beta period 'b' starts next month's cycle.
        ("2026.8.2", "b", "2026.9.0b1"),
        ("2026.12.1", "b", "2027.1.0b1"),
        # Explicit versions are passed through untouched.
        ("2026.8.0b4", "2026.9.0b1", "2026.9.0b1"),
        ("2026.8.0b4", "2026.7.3", "2026.7.3"),
    ],
)
def test_cut_resolves_version(
    commands, cut_calls, monkeypatch, latest, argument, expected
):
    from esphomerelease.model import Version

    _set_latest(commands, monkeypatch, latest)

    result = CliRunner().invoke(commands.cli, ["cut", argument])

    assert result.exit_code == 0
    expected_version = Version.parse(expected)
    kind = "beta" if expected_version.beta else "stable"
    assert cut_calls == [(kind, expected_version)]


def test_cut_shorthand_prints_resolution(commands, cut_calls, monkeypatch):
    _set_latest(commands, monkeypatch, "2026.8.0b4")

    result = CliRunner().invoke(commands.cli, ["cut", "s"])

    assert result.exit_code == 0
    assert "Latest release is 2026.8.0b4, resolved 's' to 2026.8.0" in result.output


def test_cut_explicit_version_does_not_hit_github(commands, cut_calls, monkeypatch):
    """An explicit version never looks up the latest release."""

    def _boom(**kwargs):
        raise AssertionError("latest_release should not be called")

    monkeypatch.setattr(commands.EsphomeProject, "latest_release", _boom)

    result = CliRunner().invoke(commands.cli, ["cut", "2026.8.0b5"])

    assert result.exit_code == 0


def test_cut_invalid_version(commands, cut_calls, monkeypatch):
    _set_latest(commands, monkeypatch, "2026.8.0b4")

    result = CliRunner().invoke(commands.cli, ["cut", "nonsense"])

    assert result.exit_code != 0
    assert cut_calls == []


@pytest.mark.parametrize(
    ("latest", "argument", "expected"),
    [
        ("2026.8.0b4", "b", "2026.8.0b5"),
        ("2026.8.0b4", "s", "2026.8.0"),
        ("2026.8.1", "s", "2026.8.2"),
        ("2026.8.1", "2026.9.0b1", "2026.9.0b1"),
    ],
)
def test_publish_resolves_version(
    commands, publish_calls, monkeypatch, latest, argument, expected
):
    from esphomerelease.model import Version

    _set_latest(commands, monkeypatch, latest)

    result = CliRunner().invoke(commands.cli, ["publish", argument])

    assert result.exit_code == 0
    expected_version = Version.parse(expected)
    kind = "beta" if expected_version.beta else "stable"
    assert len(publish_calls) == 1
    assert publish_calls[0][0] == kind
    assert publish_calls[0][1] == expected_version
    assert publish_calls[0][2] == [
        commands.EsphomeProject,
        commands.EsphomeDocsProject,
    ]


def test_publish_shorthand_with_code_only(commands, publish_calls, monkeypatch):
    _set_latest(commands, monkeypatch, "2026.8.0b4")

    result = CliRunner().invoke(commands.cli, ["publish", "b", "--code"])

    assert result.exit_code == 0
    assert publish_calls[0][2] == [commands.EsphomeProject]


def test_version_arg_help_in_command_help(commands):
    result = CliRunner().invoke(commands.cli, ["cut", "--help"])

    assert result.exit_code == 0
    assert "'s' for the next stable" in result.output
