"""Tests for publishing without an explicit version.

``publish`` with no VERSION argument looks for the release that was cut but
not published yet: a ``bump-<version>`` PR against the beta or stable branch
whose version is newer than the latest published release.

``commands`` imports ``.project``, which instantiates every ``Project`` at
import time and asserts each configured path is a directory. The ``modules``
fixture writes a temp ``config.json`` whose paths point at real directories so
the modules are importable, mirroring the import-safe reload pattern used
elsewhere in this repo.
"""

import importlib
import json
import types
from typing import List, Optional

import pytest
from click.testing import CliRunner


@pytest.fixture
def modules(tmp_path, monkeypatch):
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
    return project_mod, commands_mod


def _pull(head: str, *, state: str = "open", merged_at: Optional[str] = None):
    return types.SimpleNamespace(
        state=state,
        merged_at=merged_at,
        head=types.SimpleNamespace(ref=head),
    )


class FakePullsRepo:
    """Repo exposing only the PR listing used by pending_release_versions."""

    def __init__(self, pulls_by_base: dict):
        self._pulls_by_base = pulls_by_base
        self.calls: List[tuple] = []

    def pull_requests(self, *, base: str, state: str, number: int) -> list:
        self.calls.append((base, state, number))
        return list(self._pulls_by_base.get(base, []))


def _project(project_mod, tmp_path, pulls_by_base: dict, latest: str):
    from esphomerelease.model import Version

    proj = project_mod.Project(
        path=str(tmp_path / "repo"),
        shortname="esphome",
        stable_branch="release",
        beta_branch="beta",
        dev_branch="dev",
    )
    proj._repo = FakePullsRepo(pulls_by_base)
    proj.latest_release = lambda **kwargs: Version.parse(latest)
    return proj


def test_pending_release_versions_finds_beta_cut(modules, tmp_path):
    from esphomerelease.model import Version

    project_mod, _ = modules
    proj = _project(
        project_mod,
        tmp_path,
        {"beta": [_pull("bump-2026.8.0b5")]},
        latest="2026.8.0b4",
    )

    assert proj.pending_release_versions() == [Version.parse("2026.8.0b5")]
    # Both release branches are scanned, one short page each.
    assert proj._repo.calls == [
        ("beta", "all", proj.RECENT_RELEASE_PRS_TO_CHECK),
        ("release", "all", proj.RECENT_RELEASE_PRS_TO_CHECK),
    ]


def test_pending_release_versions_finds_merged_stable_cut(modules, tmp_path):
    """A release PR merged by hand still counts as pending."""
    from esphomerelease.model import Version

    project_mod, _ = modules
    proj = _project(
        project_mod,
        tmp_path,
        {
            "release": [
                _pull(
                    "bump-2026.8.1",
                    state="closed",
                    merged_at="2026-08-17T00:00:00Z",
                )
            ]
        },
        latest="2026.8.0",
    )

    assert proj.pending_release_versions() == [Version.parse("2026.8.1")]


def test_pending_release_versions_ignores_noise(modules, tmp_path):
    """Non-bump, unparseable, abandoned and already-published PRs are skipped."""
    project_mod, _ = modules
    proj = _project(
        project_mod,
        tmp_path,
        {
            "beta": [
                _pull("some-feature"),
                _pull("bump-not-a-version"),
                # Cut abandoned: closed without ever merging.
                _pull("bump-2026.8.0b6", state="closed"),
                # Already published.
                _pull("bump-2026.8.0b4", state="closed", merged_at="2026-08-10"),
            ],
            "release": [_pull("bump-2026.7.3", state="closed", merged_at="2026-07-20")],
        },
        latest="2026.8.0b4",
    )

    assert proj.pending_release_versions() == []


def test_pending_release_versions_sorted_newest_first(modules, tmp_path):
    from esphomerelease.model import Version

    project_mod, _ = modules
    proj = _project(
        project_mod,
        tmp_path,
        {
            "beta": [_pull("bump-2026.8.0b5"), _pull("bump-2026.8.0b5")],
            "release": [_pull("bump-2026.8.0")],
        },
        latest="2026.8.0b4",
    )

    assert proj.pending_release_versions() == [
        Version.parse("2026.8.0"),
        Version.parse("2026.8.0b5"),
    ]


@pytest.fixture
def publish_calls(modules, monkeypatch):
    _, commands = modules
    calls = []
    monkeypatch.setattr(
        commands.cutting,
        "publish_beta_release",
        lambda v, projects: calls.append(("beta", v)),
    )
    monkeypatch.setattr(
        commands.cutting,
        "publish_release",
        lambda v, projects: calls.append(("stable", v)),
    )
    return calls


def _set_pending(commands, monkeypatch, versions: List[str]) -> None:
    from esphomerelease.model import Version

    monkeypatch.setattr(
        commands.EsphomeProject,
        "pending_release_versions",
        lambda: [Version.parse(v) for v in versions],
    )


def test_publish_without_version_uses_pending_release(
    modules, publish_calls, monkeypatch
):
    from esphomerelease.model import Version

    _, commands = modules
    _set_pending(commands, monkeypatch, ["2026.8.0b5"])

    result = CliRunner().invoke(commands.cli, ["publish"])

    assert result.exit_code == 0
    assert publish_calls == [("beta", Version.parse("2026.8.0b5"))]
    assert "Publishing the release that was cut: 2026.8.0b5" in result.output


def test_publish_without_version_stable(modules, publish_calls, monkeypatch):
    from esphomerelease.model import Version

    _, commands = modules
    _set_pending(commands, monkeypatch, ["2026.8.1"])

    result = CliRunner().invoke(commands.cli, ["publish"])

    assert result.exit_code == 0
    assert publish_calls == [("stable", Version.parse("2026.8.1"))]


def test_publish_without_version_none_pending(modules, publish_calls, monkeypatch):
    _, commands = modules
    _set_pending(commands, monkeypatch, [])

    result = CliRunner().invoke(commands.cli, ["publish"])

    assert result.exit_code != 0
    assert "no cut-but-unpublished release" in result.output
    assert publish_calls == []


def test_publish_without_version_multiple_pending(modules, publish_calls, monkeypatch):
    _, commands = modules
    _set_pending(commands, monkeypatch, ["2026.8.0", "2026.8.0b5"])

    result = CliRunner().invoke(commands.cli, ["publish"])

    assert result.exit_code != 0
    assert (
        "multiple cut-but-unpublished releases (2026.8.0, 2026.8.0b5)" in result.output
    )
    assert publish_calls == []


def test_publish_with_version_skips_lookup(modules, publish_calls, monkeypatch):
    """An explicit version never looks for a pending release."""
    from esphomerelease.model import Version

    _, commands = modules

    def _boom():
        raise AssertionError("pending_release_versions should not be called")

    monkeypatch.setattr(commands.EsphomeProject, "pending_release_versions", _boom)

    result = CliRunner().invoke(commands.cli, ["publish", "2026.8.0b5"])

    assert result.exit_code == 0
    assert publish_calls == [("beta", Version.parse("2026.8.0b5"))]
