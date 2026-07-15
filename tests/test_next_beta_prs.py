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
    """Issue as it appears in a milestone listing.

    ``pr=True`` gives it the ``pull_request`` payload block github3 exposes as
    ``pull_request_urls`` (with ``merged_at`` set for merged PRs); plain issues
    keep it ``None``. Labels are payload labels (``original_labels``).
    """

    def __init__(
        self,
        number: int,
        labels: Optional[List[str]] = None,
        *,
        pr: bool = False,
        merged_at: Optional[str] = None,
        title: str = "title",
    ):
        self.number = number
        self.title = title
        self.original_labels = [FakeLabel(name) for name in labels or []]
        self.pull_request_urls: Optional[dict] = None
        if pr:
            self.pull_request_urls = {
                "html_url": f"https://github.com/esphome/esphome/pull/{number}",
                "merged_at": merged_at,
            }
        self.milestone_edits: List[int] = []

    def edit(self, *, milestone: int) -> None:
        self.milestone_edits.append(milestone)


class FakePull:
    def __init__(
        self,
        number: int,
        *,
        title: str = "title",
        merged_at: Optional[datetime] = None,
        login: str = "alice",
        merge_commit_sha: Optional[str] = None,
    ):
        self.number = number
        self.title = title
        self.html_url = f"https://github.com/esphome/esphome/pull/{number}"
        self.user = types.SimpleNamespace(login=login)
        self.merged_at = merged_at or datetime(2026, 7, 1)
        self.merge_commit_sha = merge_commit_sha or f"sha{number}"


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
        self.pull_request_calls: List[int] = []

    def milestones(self, state: str) -> list:
        assert state == "open"
        return self._milestones

    def issues(self, *, milestone: int, state: str) -> List[FakeIssue]:
        return self._issues[state]

    def pull_request(self, number: int) -> FakePull:
        self.pull_request_calls.append(number)
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
    remaining PRs come back sorted by merge time. Only surviving PRs are
    fetched from the API — everything else is filtered from the issue
    listing payload."""
    project_mod, _ = modules
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")

    later = FakePull(4, merged_at=datetime(2026, 7, 2))
    earlier = FakePull(5, merged_at=datetime(2026, 7, 1))
    repo = FakeRepo(
        closed_issues=[
            FakeIssue(1),  # plain issue, not a PR
            FakeIssue(2, pr=True),  # closed but unmerged PR
            FakeIssue(
                3, labels=["cherry-picked"], pr=True, merged_at="2026-07-01T00:00:00Z"
            ),  # already picked
            FakeIssue(4, pr=True, merged_at="2026-07-02T00:00:00Z"),
            FakeIssue(5, pr=True, merged_at="2026-07-01T00:00:00Z"),
        ],
        pulls={
            4: later,
            5: earlier,
        },
    )
    proj._repo = repo

    assert proj.get_next_beta_prs_for_milestone(MILESTONE) == [earlier, later]
    # No per-issue probing: only the PRs in the result were fetched.
    assert sorted(repo.pull_request_calls) == [4, 5]


def test_next_beta_prs_command_lists_prs(modules):
    """The command drives the real milestone/PR lookups through a fake repo."""
    _, commands = modules

    commands.EsphomeProject._repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(10, pr=True, merged_at="2026-07-01T00:00:00Z")],
        open_issues=[FakeIssue(11, pr=True), FakeIssue(12)],
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


def test_get_prs_fetches_missing_once_and_uses_cache(modules, tmp_path):
    """Cached PRs are not re-fetched; duplicates are fetched only once."""
    project_mod, _ = modules
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")

    cached = FakePull(1)
    fetched = FakePull(2)
    repo = FakeRepo(pulls={2: fetched})
    proj._repo = repo
    proj.pr_cache[1] = cached

    assert proj.get_prs([1, 2, 2, 1]) == [cached, fetched, fetched, cached]
    assert repo.pull_request_calls == [2]

    # Second call is served entirely from the cache.
    assert proj.get_prs([1, 2]) == [cached, fetched]
    assert repo.pull_request_calls == [2]


def test_get_open_prs_no_milestone(modules, tmp_path):
    project_mod, _ = modules
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")
    assert proj.get_open_prs_for_milestone(None) == []


def test_cherry_pick_from_milestone_no_milestone(modules, tmp_path):
    project_mod, _ = modules
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")
    assert proj.cherry_pick_from_milestone(None) == []


def test_cherry_pick_from_milestone_filters_prompts_and_picks_in_order(
    modules, tmp_path, monkeypatch
):
    """Plain issues are skipped, unmerged PRs prompt, cherry-picked PRs are
    reported, and the rest are picked sorted by merge time."""
    import click

    project_mod, _ = modules
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")

    unmerged = FakeIssue(2, pr=True, title="Unmerged PR")
    picked = FakeIssue(
        3, labels=["cherry-picked"], pr=True, merged_at="2026-07-01T00:00:00Z"
    )
    second = FakeIssue(4, pr=True, merged_at="2026-07-02T00:00:00Z")
    first = FakeIssue(5, pr=True, merged_at="2026-07-01T00:00:00Z")
    repo = FakeRepo(
        closed_issues=[FakeIssue(1), unmerged, picked, second, first],
        pulls={
            4: FakePull(4, merged_at=datetime(2026, 7, 2)),
            5: FakePull(5, merged_at=datetime(2026, 7, 1)),
        },
    )
    proj._repo = repo

    prompts = []
    # First answer "no" to exercise the ask-again loop, then confirm.
    answers = iter([False, True])
    monkeypatch.setattr(
        click,
        "confirm",
        lambda text, **kwargs: prompts.append(text) or next(answers),
    )
    picked_shas = []
    monkeypatch.setattr(proj, "cherry_pick", picked_shas.append)

    result = proj.cherry_pick_from_milestone(MILESTONE)

    assert result == [first, second]
    assert picked_shas == ["sha5", "sha4"]
    # Only the actually-picked PRs were fetched from the API.
    assert sorted(repo.pull_request_calls) == [4, 5]
    assert len(prompts) == 2 and "Unmerged PR" in prompts[0]


def test_remove_merged_prs_from_milestone(modules, tmp_path):
    """Merged PRs get their milestone cleared without any PR fetches; plain
    issues and unmerged PRs are left alone."""
    project_mod, _ = modules
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")

    assert proj.remove_merged_prs_from_milestone(None) == []

    plain = FakeIssue(1)
    unmerged = FakeIssue(2, pr=True)
    merged = FakeIssue(3, pr=True, merged_at="2026-07-01T00:00:00Z")
    repo = FakeRepo(closed_issues=[plain, unmerged, merged])
    proj._repo = repo

    assert proj.remove_merged_prs_from_milestone(MILESTONE) == [merged]
    assert merged.milestone_edits == [0]
    assert plain.milestone_edits == [] and unmerged.milestone_edits == []
    assert repo.pull_request_calls == []


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
