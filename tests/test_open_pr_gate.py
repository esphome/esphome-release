"""Tests for the "are there open PRs on the milestone?" pre-flight gate.

``cutting._check_open_milestone_prs`` blocks full releases (``block=True``)
until the cycle milestone has no open PRs left, and only warns for betas
(``block=False``). A patch stable release (e.g. ``2026.6.1``) additionally
offers an "ignore" choice: the open PRs are moved onto the next patch
milestone via ``_move_open_prs_to_next_patch_milestone`` /
``Project.move_open_prs_to_milestone`` and the cut carries on. A ``.0``
release cannot ignore - there is no obvious next milestone for those PRs -
so it keeps the plain yes/no ``click.confirm("Check again?")`` prompt.

``cutting`` imports ``.project``, which instantiates every ``Project`` at
import time and asserts each configured path is a directory. The shared
``cutting`` fixture (see ``conftest.py``) writes a temp ``config.json`` whose
paths point at real directories so the module is importable. Fakes are
hand-rolled (no ``unittest.mock``); they record their call lists so tests can
assert exactly what happened, including when nothing should have happened at
all (e.g. no prompt call, no milestone creation).
"""

from typing import Any, Dict, List, Optional

import click
import pytest

from esphomerelease.exceptions import EsphomeReleaseError
from esphomerelease.model import Version


class FakeIssue:
    """A pull request as it appears in a milestone's open-issue listing."""

    def __init__(self, number: int, *, is_pr: bool = True) -> None:
        self.number = number
        self.pull_request_urls: Optional[dict] = {"x": "y"} if is_pr else None
        self.edit_calls: List[Dict[str, Any]] = []

    def edit(self, **kwargs: Any) -> None:
        self.edit_calls.append(kwargs)


class FakePull:
    """The PR object returned by ``repo.pull_request(number)``."""

    def __init__(self, number: int, *, title: str = "title", repo: str = "esphome") -> None:
        self.number = number
        self.title = title
        self.html_url = f"https://github.com/esphome/{repo}/pull/{number}"


class FakeMilestone:
    def __init__(self, title: str, number: int) -> None:
        self.title = title
        self.number = number


class FakeRepo:
    """Just enough of ``github3.Repository`` for the open-PR gate.

    ``open_issues`` maps a milestone number to the (mutable) list of open-PR
    issues on it, so a test can simulate a PR closing between two checks by
    mutating the list the fake already holds a reference to.
    """

    def __init__(
        self,
        *,
        milestones: Optional[List[FakeMilestone]] = None,
        open_issues: Optional[Dict[int, List[FakeIssue]]] = None,
        pulls: Optional[Dict[int, FakePull]] = None,
    ) -> None:
        self._milestones: List[FakeMilestone] = milestones or []
        self._open_issues: Dict[int, List[FakeIssue]] = open_issues or {}
        self._pulls: Dict[int, FakePull] = pulls or {}
        self.issues_calls: List[tuple] = []
        self.create_milestone_calls: List[tuple] = []
        self.pull_request_calls: List[int] = []
        self._next_number = 1000

    def milestones(self, state: str) -> List[FakeMilestone]:
        assert state == "open"
        return list(self._milestones)

    def issues(self, *, milestone: int, state: str) -> List[FakeIssue]:
        self.issues_calls.append((milestone, state))
        if state != "open":
            return []
        return list(self._open_issues.get(milestone, []))

    def create_milestone(self, title: str, *, due_on: Optional[str] = None) -> FakeMilestone:
        self.create_milestone_calls.append((title, due_on))
        ms = FakeMilestone(title, self._next_number)
        self._next_number += 1
        self._milestones.append(ms)
        return ms

    def pull_request(self, number: int) -> FakePull:
        self.pull_request_calls.append(number)
        return self._pulls[number]


def _wire(cutting: Any, *, code_repo: FakeRepo, docs_repo: FakeRepo) -> None:
    """Inject fake repos, bypassing the lazy ``repo`` property and real session."""
    cutting.EsphomeProject._repo = code_repo
    cutting.EsphomeProject.pr_cache.clear()
    cutting.EsphomeDocsProject._repo = docs_repo
    cutting.EsphomeDocsProject.pr_cache.clear()


def _no_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if either prompt style is invoked."""
    monkeypatch.setattr(
        click, "confirm", lambda *a, **k: pytest.fail("should not prompt (confirm)")
    )
    monkeypatch.setattr(
        click, "prompt", lambda *a, **k: pytest.fail("should not prompt (prompt)")
    )


PATCH_VERSION = Version.parse("2026.6.1")
FULL_VERSION = Version.parse("2026.6.0")
BETA_VERSION = Version.parse("2026.6.0b2")


def test_no_open_prs_returns_without_prompting(cutting, monkeypatch, capsys):
    """A milestone with nothing open on it clears the gate immediately."""
    milestone = FakeMilestone("2026.6.1", 1)
    code_repo = FakeRepo(milestones=[milestone], open_issues={})
    docs_repo = FakeRepo(milestones=[milestone], open_issues={})
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    _no_prompts(monkeypatch)

    cutting._check_open_milestone_prs(PATCH_VERSION, block=True)

    assert "Warning" not in capsys.readouterr().out


def test_missing_milestone_returns_without_prompting(cutting, monkeypatch, capsys):
    """Neither project has the milestone at all: nothing to gate on."""
    code_repo = FakeRepo(milestones=[])
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    _no_prompts(monkeypatch)

    cutting._check_open_milestone_prs(PATCH_VERSION, block=True)

    assert code_repo.issues_calls == []
    assert docs_repo.issues_calls == []
    assert "Warning" not in capsys.readouterr().out


def test_beta_cut_warns_and_returns_without_prompting(cutting, monkeypatch, capsys):
    """block=False (betas) only warns; it never blocks, even with open PRs."""
    milestone = FakeMilestone("2026.6.0", 1)
    issue = FakeIssue(555)
    code_repo = FakeRepo(
        milestones=[milestone],
        open_issues={1: [issue]},
        pulls={555: FakePull(555, title="Fix the thing")},
    )
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    _no_prompts(monkeypatch)

    cutting._check_open_milestone_prs(BETA_VERSION, block=False)

    out = capsys.readouterr().out
    assert "Found 1 open PR(s) on the 2026.6.0 milestone" in out
    assert "[esphome] #555: Fix the thing (https://github.com/esphome/esphome/pull/555)" in out
    assert issue.edit_calls == []


def _make_prompt(answer: str, *, on_call=None):
    """A fake ``click.prompt`` returning ``answer`` and recording its kwargs."""
    calls: List[tuple] = []

    def prompt(text: str, **kwargs: Any) -> str:
        calls.append((text, kwargs))
        if on_call is not None:
            on_call()
        return answer

    return prompt, calls


def test_patch_release_ignore_moves_prs_with_existing_target_milestone(
    cutting, monkeypatch, capsys
):
    """Answering 'i' really moves the PR via Project.move_open_prs_to_milestone,
    reusing the 2026.6.2 milestone since it already exists."""
    milestone = FakeMilestone("2026.6.1", 1)
    target = FakeMilestone("2026.6.2", 2)
    issue = FakeIssue(700)
    code_repo = FakeRepo(
        milestones=[milestone, target],
        open_issues={1: [issue]},
        pulls={700: FakePull(700, title="A patch fix")},
    )
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    prompt, calls = _make_prompt("i")
    monkeypatch.setattr(click, "prompt", prompt)
    monkeypatch.setattr(
        click, "confirm", lambda *a, **k: pytest.fail("should not use confirm")
    )

    cutting._check_open_milestone_prs(PATCH_VERSION, block=True)

    assert len(calls) == 1
    assert issue.edit_calls == [{"milestone": 2}]
    assert code_repo.create_milestone_calls == []

    out = capsys.readouterr().out
    assert "Moved [esphome] #700 to the 2026.6.2 milestone" in out


def test_patch_release_ignore_creates_missing_target_milestone(cutting, monkeypatch, capsys):
    """When 2026.6.2 does not exist yet, it is created exactly once."""
    milestone = FakeMilestone("2026.6.1", 1)
    issue = FakeIssue(701)
    code_repo = FakeRepo(
        milestones=[milestone],
        open_issues={1: [issue]},
        pulls={701: FakePull(701, title="Another patch fix")},
    )
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    prompt, calls = _make_prompt("i")
    monkeypatch.setattr(click, "prompt", prompt)

    cutting._check_open_milestone_prs(PATCH_VERSION, block=True)

    assert len(calls) == 1
    assert code_repo.create_milestone_calls == [("2026.6.2", None)]
    created_number = code_repo._milestones[-1].number
    assert issue.edit_calls == [{"milestone": created_number}]

    out = capsys.readouterr().out
    assert "Moved [esphome] #701 to the 2026.6.2 milestone" in out


def test_patch_release_decline_aborts_without_moving(cutting, monkeypatch, capsys):
    """Answering 'n' aborts the cut and leaves the PR untouched."""
    milestone = FakeMilestone("2026.6.1", 1)
    issue = FakeIssue(702)
    code_repo = FakeRepo(
        milestones=[milestone],
        open_issues={1: [issue]},
        pulls={702: FakePull(702)},
    )
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    prompt, calls = _make_prompt("n")
    monkeypatch.setattr(click, "prompt", prompt)

    with pytest.raises(EsphomeReleaseError, match="open PRs on milestone"):
        cutting._check_open_milestone_prs(PATCH_VERSION, block=True)

    assert len(calls) == 1
    assert issue.edit_calls == []
    assert code_repo.create_milestone_calls == []


def test_patch_release_recheck_then_clears(cutting, monkeypatch, capsys):
    """Answering 'y' loops; the PR closes in between and the second pass clears.

    Also checks the prompt text and the exact ``click.Choice``/default passed.
    """
    milestone = FakeMilestone("2026.6.1", 1)
    issue = FakeIssue(703)
    open_issues = {1: [issue]}
    code_repo = FakeRepo(
        milestones=[milestone],
        open_issues=open_issues,
        pulls={703: FakePull(703)},
    )
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    def close_the_pr() -> None:
        open_issues[1] = []

    prompt, calls = _make_prompt("y", on_call=close_the_pr)
    monkeypatch.setattr(click, "prompt", prompt)

    cutting._check_open_milestone_prs(PATCH_VERSION, block=True)

    assert len(calls) == 1
    text, kwargs = calls[0]
    assert "[i] ignore" in text
    assert "2026.6.2" in text
    assert kwargs["type"].choices == ["y", "i", "n"]
    assert kwargs["default"] == "y"
    assert issue.edit_calls == []


def test_full_zero_release_uses_plain_confirm_decline_aborts(cutting, monkeypatch, capsys):
    """A .0 release (patch == 0) cannot ignore: plain confirm, declining aborts."""
    milestone = FakeMilestone("2026.6.0", 1)
    issue = FakeIssue(704)
    code_repo = FakeRepo(
        milestones=[milestone],
        open_issues={1: [issue]},
        pulls={704: FakePull(704)},
    )
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    monkeypatch.setattr(click, "prompt", lambda *a, **k: pytest.fail("should not use prompt"))
    confirm_calls = []

    def confirm(text: str, **kwargs: Any) -> bool:
        confirm_calls.append(text)
        return False

    monkeypatch.setattr(click, "confirm", confirm)

    with pytest.raises(EsphomeReleaseError, match="open PRs on milestone"):
        cutting._check_open_milestone_prs(FULL_VERSION, block=True)

    assert len(confirm_calls) == 1
    assert "Check again?" in confirm_calls[0]
    assert issue.edit_calls == []


def test_full_zero_release_uses_plain_confirm_accept_then_clears(cutting, monkeypatch, capsys):
    """Accepting the plain confirm loops; the PR is gone on the second pass."""
    milestone = FakeMilestone("2026.6.0", 1)
    issue = FakeIssue(705)
    open_issues = {1: [issue]}
    code_repo = FakeRepo(
        milestones=[milestone],
        open_issues=open_issues,
        pulls={705: FakePull(705)},
    )
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    monkeypatch.setattr(click, "prompt", lambda *a, **k: pytest.fail("should not use prompt"))
    confirm_calls = []

    def confirm(text: str, **kwargs: Any) -> bool:
        confirm_calls.append(text)
        open_issues[1] = []
        return True

    monkeypatch.setattr(click, "confirm", confirm)

    cutting._check_open_milestone_prs(FULL_VERSION, block=True)

    assert len(confirm_calls) == 1


def test_ignore_moves_prs_across_both_projects_skipping_missing_milestone(
    cutting, monkeypatch, capsys
):
    """Open PRs on both esphome and esphome.io get moved; a project missing the
    milestone entirely (docs here) is skipped without error."""
    milestone = FakeMilestone("2026.6.1", 1)
    code_issue = FakeIssue(710)
    code_repo = FakeRepo(
        milestones=[milestone],
        open_issues={1: [code_issue]},
        pulls={710: FakePull(710, title="Code fix")},
    )

    docs_milestone = FakeMilestone("2026.6.1", 9)
    docs_issue = FakeIssue(20)
    docs_repo = FakeRepo(
        milestones=[docs_milestone],
        open_issues={9: [docs_issue]},
        pulls={20: FakePull(20, title="Docs fix", repo="esphome.io")},
    )
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    prompt, calls = _make_prompt("i")
    monkeypatch.setattr(click, "prompt", prompt)

    cutting._check_open_milestone_prs(PATCH_VERSION, block=True)

    assert len(calls) == 1
    assert code_issue.edit_calls != []
    assert docs_issue.edit_calls != []

    out = capsys.readouterr().out
    assert "Moved [esphome] #710 to the 2026.6.2 milestone" in out
    assert "Moved [docs] #20 to the 2026.6.2 milestone" in out


def test_move_open_prs_to_milestone_none_milestone_returns_empty(cutting):
    """No milestone on this project at all: nothing to look up or move."""
    code_repo = FakeRepo(milestones=[])
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    result = cutting.EsphomeProject.move_open_prs_to_milestone(None, "2026.6.2")

    assert result == []
    assert code_repo.create_milestone_calls == []


def test_move_open_prs_to_milestone_no_open_issues_skips_target_creation(cutting):
    """An empty milestone never triggers ensure_milestone/create_milestone."""
    milestone = FakeMilestone("2026.6.1", 1)
    code_repo = FakeRepo(milestones=[milestone], open_issues={1: []})
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    result = cutting.EsphomeProject.move_open_prs_to_milestone(milestone, "2026.6.2")

    assert result == []
    assert code_repo.create_milestone_calls == []


def test_move_open_prs_to_milestone_ignores_non_pr_issues(cutting):
    """A plain issue (pull_request_urls is None) on the milestone is not a PR
    and is left alone."""
    milestone = FakeMilestone("2026.6.1", 1)
    plain_issue = FakeIssue(800, is_pr=False)
    code_repo = FakeRepo(milestones=[milestone], open_issues={1: [plain_issue]})
    docs_repo = FakeRepo(milestones=[])
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    result = cutting.EsphomeProject.move_open_prs_to_milestone(milestone, "2026.6.2")

    assert result == []
    assert plain_issue.edit_calls == []
    assert code_repo.create_milestone_calls == []
