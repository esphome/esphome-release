"""Tests for the ``next-beta-prs`` subcommand.

The command lists the PRs on the cycle milestone that the next beta cut would
cherry-pick: merged PRs without the ``cherry-picked`` label, in merge order.

``commands`` imports ``.project``, which instantiates every ``Project`` at
import time and asserts each configured path is a directory. The ``commands``
fixture writes a temp ``config.json`` whose paths point at real directories so
the modules are importable, mirroring the import-safe reload pattern used
elsewhere in this repo.
"""

import importlib
import json
import types
from datetime import datetime
from typing import List, Optional

import pytest
from click.testing import CliRunner
from github3.exceptions import NotFoundError


@pytest.fixture
def modules(tmp_path, monkeypatch):
    """Reload config/project/cutting/commands against a temp config.json."""
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


def _not_found_error() -> NotFoundError:
    resp = types.SimpleNamespace(
        status_code=404,
        content=b"",
        json=lambda: {"message": "Not Found"},
    )
    return NotFoundError(resp)


class FakeLabel:
    def __init__(self, name: str):
        self.name = name


class FakeIssue:
    def __init__(self, number: int, labels: Optional[List[str]] = None):
        self.number = number
        self._labels = [FakeLabel(name) for name in labels or []]

    def labels(self) -> List[FakeLabel]:
        return self._labels


class FakePull:
    def __init__(
        self,
        number: int,
        *,
        title: str = "title",
        merged: bool = True,
        merged_at: Optional[datetime] = None,
        login: str = "alice",
    ):
        self.number = number
        self.title = title
        self.html_url = f"https://github.com/esphome/esphome/pull/{number}"
        self.user = types.SimpleNamespace(login=login)
        self._merged = merged
        self.merged_at = merged_at or datetime(2026, 7, 1)

    def is_merged(self) -> bool:
        return self._merged


class FakeRepo:
    def __init__(
        self,
        *,
        milestones: Optional[list] = None,
        closed_issues: Optional[List[FakeIssue]] = None,
        open_issues: Optional[List[FakeIssue]] = None,
        pulls: Optional[dict] = None,
    ):
        self._milestones = milestones or []
        self._issues = {
            "closed": closed_issues or [],
            "open": open_issues or [],
        }
        self._pulls = pulls or {}

    def milestones(self, state: str) -> list:
        assert state == "open"
        return self._milestones

    def issues(self, *, milestone: int, state: str) -> List[FakeIssue]:
        return self._issues[state]

    def pull_request(self, number: int) -> FakePull:
        pull = self._pulls.get(number)
        if pull is None:
            raise _not_found_error()
        return pull


MILESTONE = types.SimpleNamespace(title="2026.7.0", number=5)


def test_get_next_beta_prs_no_milestone(modules, tmp_path):
    project_mod, _ = modules
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")
    assert proj.get_next_beta_prs_for_milestone(None) == []


def test_get_next_beta_prs_filters_and_sorts(modules, tmp_path):
    """Plain issues, unmerged PRs and cherry-picked PRs are skipped;
    remaining PRs come back sorted by merge time."""
    project_mod, _ = modules
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")

    later = FakePull(4, merged_at=datetime(2026, 7, 2))
    earlier = FakePull(5, merged_at=datetime(2026, 7, 1))
    proj._repo = FakeRepo(
        closed_issues=[
            FakeIssue(1),  # plain issue, not a PR
            FakeIssue(2),  # closed but unmerged PR
            FakeIssue(3, labels=["cherry-picked"]),  # already picked
            FakeIssue(4),
            FakeIssue(5),
        ],
        pulls={
            2: FakePull(2, merged=False),
            3: FakePull(3),
            4: later,
            5: earlier,
        },
    )

    assert proj.get_next_beta_prs_for_milestone(MILESTONE) == [earlier, later]


def test_next_beta_prs_command_lists_prs(modules):
    """The command drives the real milestone/PR lookups through a fake repo."""
    _, commands = modules

    commands.EsphomeProject._repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(10)],
        open_issues=[FakeIssue(11), FakeIssue(12)],
        pulls={
            10: FakePull(10, title="Merged fix", login="alice"),
            11: FakePull(11, title="Open fix"),
            # 12 is a plain open issue, not a PR
        },
    )
    # Docs project has no matching milestone.
    commands.EsphomeDocsProject._repo = FakeRepo(milestones=[])

    result = CliRunner().invoke(commands.cli, ["next-beta-prs", "2026.7.0"])

    assert result.exit_code == 0
    assert "esphome: 1 PR(s) on milestone 2026.7.0 will be in the next beta" in result.output
    assert "#10 Merged fix by @alice" in result.output
    assert "1 open PR(s) still on the milestone" in result.output
    assert "#11 Open fix" in result.output
    assert "Couldn't find milestone 2026.7.0 for project esphome.io" in result.output


def test_next_beta_prs_command_no_open_prs(modules):
    """Without open milestone PRs the warning block is skipped."""
    _, commands = modules

    for proj in (commands.EsphomeProject, commands.EsphomeDocsProject):
        proj._repo = FakeRepo(milestones=[MILESTONE])

    result = CliRunner().invoke(commands.cli, ["next-beta-prs", "2026.7.0"])

    assert result.exit_code == 0
    assert "open PR(s)" not in result.output


def test_next_beta_prs_default_milestone_during_beta(modules, monkeypatch):
    """With no argument during a beta period, the beta's cycle milestone is used."""
    from esphomerelease.model import Version

    _, commands = modules
    monkeypatch.setattr(
        commands.EsphomeProject,
        "latest_release",
        lambda **k: Version.parse("2026.7.0b2"),
    )
    for proj in (commands.EsphomeProject, commands.EsphomeDocsProject):
        proj._repo = FakeRepo(milestones=[MILESTONE])

    result = CliRunner().invoke(commands.cli, ["next-beta-prs"])

    assert result.exit_code == 0
    assert "milestone 2026.7.0" in result.output


def test_next_beta_prs_default_milestone_after_stable(modules, monkeypatch):
    """With no argument after a stable release, next month's cycle is used."""
    from esphomerelease.model import Version

    _, commands = modules
    monkeypatch.setattr(
        commands.EsphomeProject,
        "latest_release",
        lambda **k: Version.parse("2026.6.2"),
    )
    for proj in (commands.EsphomeProject, commands.EsphomeDocsProject):
        proj._repo = FakeRepo(milestones=[])

    result = CliRunner().invoke(commands.cli, ["next-beta-prs"])

    assert result.exit_code == 0
    assert "Couldn't find milestone 2026.7.0" in result.output
