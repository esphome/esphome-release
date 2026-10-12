"""Tests for the cut's "are the linked docs PRs merged?" pre-flight check.

``cutting._check_linked_docs_prs`` closes the gap left by
``_check_open_milestone_prs``: that one only sees PRs carrying the cycle
milestone, and docs PRs frequently have no milestone at all. This check starts
from the code PRs actually going into the cut, reads the docs PR(s) each one
links to, and blocks while a *confirmed* pair (both bodies reference each other)
has an unmerged docs PR. One-way references are reported but never block.

A blocking pair raises a three-way ``click.prompt`` (``y`` check again, ``i``
ignore and cut anyway because the docs land later, ``n`` abort). ``i`` touches
no milestone: it only reports which code PRs ship undocumented and returns.

``cutting`` imports ``.project``, which instantiates every ``Project`` at import
time and asserts each configured path is a directory. The ``modules`` fixture
writes a temp ``config.json`` whose paths point at real directories so the
modules are importable, mirroring the import-safe reload pattern used elsewhere
in this repo. Fakes are hand-rolled (no ``unittest.mock``) and record their call
lists so tests can assert no extra API calls were made.
"""

import importlib
import json
import types
from datetime import datetime
from typing import Any, Callable, List, Optional

import click
import pytest

from esphomerelease.exceptions import EsphomeReleaseError
from esphomerelease.model import Version


@pytest.fixture
def modules(tmp_path, monkeypatch):
    """Reload config/project/cutting against a temp config.json."""
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
    return cutting_mod


MILESTONE = types.SimpleNamespace(
    title="2026.7.0", number=5, closed_issues=2, open_issues=0
)

VERSION = Version.parse("2026.7.0b2")


def _back_link(docs_number: int) -> str:
    """A code PR body filled in with the docs PR it pairs with."""
    return (
        "**Pull request in [esphome.io](https://github.com/esphome/esphome.io) "
        "with documentation (if applicable):**\n"
        f"- esphome/esphome.io#{docs_number}\n"
    )


def _docs_body(code_number: int) -> str:
    """A docs PR body filled in with the code PR it pairs with."""
    return (
        "**Pull request in esphome with YAML changes (if applicable):**\n"
        f"- esphome/esphome#{code_number}\n"
    )


class FakeLabel:
    def __init__(self, name: str):
        self.name = name


class FakeIssue:
    """A merged PR as it appears in the milestone issue listing."""

    def __init__(self, number: int, labels: Optional[List[str]] = None):
        self.number = number
        self.original_labels = [FakeLabel(name) for name in labels or []]
        self.pull_request_urls = {"merged_at": "2026-07-01T00:00:00Z"}


class FakePull:
    def __init__(
        self,
        number: int,
        *,
        body: str = "",
        title: str = "title",
        merged_at: Optional[datetime] = None,
        repo: str = "esphome",
    ):
        self.number = number
        self.title = title
        self.body = body
        self.merged_at = merged_at
        self.html_url = f"https://github.com/esphome/{repo}/pull/{number}"


class FakeRepo:
    def __init__(
        self,
        *,
        milestones: Optional[list] = None,
        closed_issues: Optional[List[FakeIssue]] = None,
        pulls: Optional[dict] = None,
    ):
        self._milestones = milestones or []
        self._closed_issues = closed_issues or []
        self._pulls = pulls or {}
        self.pull_request_calls: List[int] = []
        self.issues_calls: List[tuple] = []

    def milestones(self, state: str) -> list:
        assert state == "open"
        return self._milestones

    def issues(self, *, milestone: int, state: str) -> List[FakeIssue]:
        self.issues_calls.append((milestone, state))
        return self._closed_issues

    def pull_request(self, number: int) -> FakePull:
        self.pull_request_calls.append(number)
        return self._pulls[number]


def _wire(cutting, *, code_repo: FakeRepo, docs_repo: FakeRepo) -> None:
    """Inject fake repos, bypassing the lazy ``repo`` property and real session."""
    cutting.EsphomeProject._repo = code_repo
    cutting.EsphomeProject.pr_cache.clear()
    cutting.EsphomeDocsProject._repo = docs_repo
    cutting.EsphomeDocsProject.pr_cache.clear()


def _no_confirm(monkeypatch) -> None:
    """Fail the test if either prompt style is invoked."""
    monkeypatch.setattr(
        click, "confirm", lambda *a, **k: pytest.fail("should not prompt (confirm)")
    )
    monkeypatch.setattr(
        click, "prompt", lambda *a, **k: pytest.fail("should not prompt (prompt)")
    )


def _make_prompt(
    *answers: str, on_call: Optional[Callable[[], None]] = None
) -> tuple[Callable[..., str], List[tuple]]:
    """A fake ``click.prompt`` returning ``answers`` in turn, recording its calls.

    Each call appends ``(text, kwargs)`` to the returned list. ``on_call`` runs
    before the answer is returned, e.g. to merge a docs PR mid-prompt. Asking
    more times than there are answers fails the test.
    """
    calls: List[tuple] = []
    remaining = list(answers)

    def prompt(text: str, **kwargs: Any) -> str:
        calls.append((text, kwargs))
        if on_call is not None:
            on_call()
        if not remaining:
            pytest.fail("prompted more times than expected")
        return remaining.pop(0)

    return prompt, calls


def test_no_cycle_milestone_passes_without_touching_repos(modules, monkeypatch, capsys):
    """Without a milestone there is nothing being cut, so nothing to check."""
    cutting = modules
    code_repo = FakeRepo(milestones=[])
    docs_repo = FakeRepo()
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    _no_confirm(monkeypatch)

    cutting._check_linked_docs_prs(VERSION)

    assert code_repo.issues_calls == []
    assert docs_repo.pull_request_calls == []


def test_code_prs_without_docs_links_pass_without_docs_lookups(
    modules, monkeypatch, capsys
):
    """A code PR with an unfilled template placeholder links no docs PR, so the
    docs repo is never queried."""
    cutting = modules
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(100)],
        pulls={
            100: FakePull(
                100,
                merged_at=datetime(2026, 7, 1),
                body="- esphome/esphome.io#<esphome.io PR number goes here>",
            )
        },
    )
    docs_repo = FakeRepo()
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    _no_confirm(monkeypatch)

    cutting._check_linked_docs_prs(VERSION)

    assert code_repo.pull_request_calls == [100]
    assert docs_repo.pull_request_calls == []


def test_merged_docs_pr_passes(modules, monkeypatch, capsys):
    """A confirmed pair whose docs PR is already merged clears the gate."""
    cutting = modules
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(100)],
        pulls={
            100: FakePull(100, merged_at=datetime(2026, 7, 1), body=_back_link(7071))
        },
    )
    docs_repo = FakeRepo(
        pulls={
            7071: FakePull(
                7071,
                body=_docs_body(100),
                merged_at=datetime(2026, 7, 2),
                repo="esphome.io",
            )
        }
    )
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    _no_confirm(monkeypatch)

    cutting._check_linked_docs_prs(VERSION)

    assert docs_repo.pull_request_calls == [7071]
    assert "unmerged docs PR" not in capsys.readouterr().out


def test_cherry_picked_code_prs_are_not_rechecked(modules, monkeypatch, capsys):
    """Only the PRs the next cut will pick are considered; PRs already in an
    earlier beta carry the ``cherry-picked`` label and were gated back then."""
    cutting = modules
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(100, labels=["cherry-picked"])],
        pulls={100: FakePull(100, body=_back_link(7071))},
    )
    docs_repo = FakeRepo()
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    _no_confirm(monkeypatch)

    cutting._check_linked_docs_prs(VERSION)

    assert code_repo.pull_request_calls == []
    assert docs_repo.pull_request_calls == []


def test_one_way_reference_is_reported_but_does_not_block(modules, monkeypatch, capsys):
    """The code PR names a docs PR that does not name it back: unconfirmed, so
    it is informational only even though that docs PR is still open."""
    cutting = modules
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(14255)],
        pulls={
            14255: FakePull(
                14255, merged_at=datetime(2026, 7, 1), body=_back_link(6676)
            )
        },
    )
    docs_repo = FakeRepo(
        pulls={
            6676: FakePull(
                6676,
                title="Unrelated docs change",
                body="Documents something else entirely.",
                repo="esphome.io",
            )
        }
    )
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    _no_confirm(monkeypatch)

    cutting._check_linked_docs_prs(VERSION)

    out = capsys.readouterr().out
    assert "one-way docs PR reference(s)" in out
    assert "esphome#14255 mentions docs#6676" in out
    assert "unmerged docs PR" not in out


def test_unmerged_docs_pr_blocks_then_passes_on_recheck(modules, monkeypatch, capsys):
    """The gate blocks, the user merges the docs PR and answers "y" to check again,
    and the retry re-fetches (caches cleared) and clears."""
    cutting = modules
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(17797)],
        pulls={
            17797: FakePull(
                17797,
                title="Arbitrate the default route",
                merged_at=datetime(2026, 7, 1),
                body=_back_link(7071),
            )
        },
    )
    docs_pull = FakePull(
        7071,
        title="Document default-route arbitration",
        body=_docs_body(17797),
        repo="esphome.io",
    )
    docs_repo = FakeRepo(pulls={7071: docs_pull})
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)

    def merge_docs_pr() -> None:
        # The user goes and merges the docs PR before answering.
        docs_pull.merged_at = datetime(2026, 7, 3)

    prompt, calls = _make_prompt("y", on_call=merge_docs_pr)
    monkeypatch.setattr(click, "prompt", prompt)

    cutting._check_linked_docs_prs(VERSION)

    assert len(calls) == 1 and "[y] check again" in calls[0][0]
    # Both passes re-fetched instead of reusing the stale cached payloads.
    assert code_repo.pull_request_calls == [17797, 17797]
    assert docs_repo.pull_request_calls == [7071, 7071]

    out = capsys.readouterr().out
    assert "1 unmerged docs PR(s) for 1 PR(s)" in out
    assert "esphome#17797 Arbitrate the default route" in out
    assert "needs docs#7071: Document default-route arbitration" in out


def test_unmerged_docs_pr_declining_recheck_aborts(modules, monkeypatch, capsys):
    """Answering "n" aborts the cut with the unmerged-docs error."""
    cutting = modules
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(17797)],
        pulls={
            17797: FakePull(
                17797, merged_at=datetime(2026, 7, 1), body=_back_link(7071)
            )
        },
    )
    docs_repo = FakeRepo(
        pulls={7071: FakePull(7071, body=_docs_body(17797), repo="esphome.io")}
    )
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    prompt, calls = _make_prompt("n")
    monkeypatch.setattr(click, "prompt", prompt)

    with pytest.raises(EsphomeReleaseError, match="unmerged docs PRs"):
        cutting._check_linked_docs_prs(VERSION)

    assert len(calls) == 1


def test_shared_docs_pr_counted_once(modules, monkeypatch, capsys):
    """Several code PRs pairing with one docs PR count it once, not per pair."""
    cutting = modules
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(20347), FakeIssue(20348), FakeIssue(20350)],
        pulls={
            20347: FakePull(
                20347, merged_at=datetime(2026, 7, 1), body=_back_link(7564)
            ),
            20348: FakePull(
                20348, merged_at=datetime(2026, 7, 1), body=_back_link(7564)
            ),
            20350: FakePull(
                20350, merged_at=datetime(2026, 7, 1), body=_back_link(7565)
            ),
        },
    )
    docs_repo = FakeRepo(
        pulls={
            7564: FakePull(
                7564,
                body=_docs_body(20347) + _docs_body(20348),
                repo="esphome.io",
            ),
            7565: FakePull(7565, body=_docs_body(20350), repo="esphome.io"),
        }
    )
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    prompt, _ = _make_prompt("n")
    monkeypatch.setattr(click, "prompt", prompt)

    with pytest.raises(EsphomeReleaseError):
        cutting._check_linked_docs_prs(VERSION)

    out = capsys.readouterr().out
    assert "2 unmerged docs PR(s) for 3 PR(s)" in out
    # Every code PR still gets its own line.
    for number in (20347, 20348, 20350):
        assert f"esphome#{number}" in out


def test_blocking_and_unconfirmed_reported_together(modules, monkeypatch, capsys):
    """A cut can have both; each is listed under its own heading."""
    cutting = modules
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(17797), FakeIssue(14255)],
        pulls={
            17797: FakePull(
                17797, merged_at=datetime(2026, 7, 1), body=_back_link(7071)
            ),
            14255: FakePull(
                14255, merged_at=datetime(2026, 7, 2), body=_back_link(6676)
            ),
        },
    )
    docs_repo = FakeRepo(
        pulls={
            7071: FakePull(7071, body=_docs_body(17797), repo="esphome.io"),
            6676: FakePull(6676, body="Unrelated.", repo="esphome.io"),
        }
    )
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    prompt, _ = _make_prompt("n")
    monkeypatch.setattr(click, "prompt", prompt)

    with pytest.raises(EsphomeReleaseError):
        cutting._check_linked_docs_prs(VERSION)

    out = capsys.readouterr().out
    assert "1 one-way docs PR reference(s)" in out
    assert "1 unmerged docs PR(s)" in out


def _unmerged_single(cutting, number: int = 17797, docs_number: int = 7071):
    """Wire one code PR with one confirmed, unmerged docs PR; return the docs PR."""
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(number)],
        pulls={
            number: FakePull(
                number, merged_at=datetime(2026, 7, 1), body=_back_link(docs_number)
            )
        },
    )
    docs_pull = FakePull(docs_number, body=_docs_body(number), repo="esphome.io")
    docs_repo = FakeRepo(pulls={docs_number: docs_pull})
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    return code_repo, docs_repo


def test_ignore_returns_and_reports_without_rechecking(modules, monkeypatch, capsys):
    """Answering "i" lets the cut through, names what ships undocumented, and
    does not loop: exactly one prompt and one fetch per repo."""
    cutting = modules
    code_repo, docs_repo = _unmerged_single(cutting)
    prompt, calls = _make_prompt("i")
    monkeypatch.setattr(click, "prompt", prompt)

    assert cutting._check_linked_docs_prs(VERSION) is None

    assert len(calls) == 1
    assert code_repo.pull_request_calls == [17797]
    assert docs_repo.pull_request_calls == [7071]
    out = capsys.readouterr().out
    assert "Ignoring 1 unmerged docs PR(s); shipping esphome#17797 undocumented" in out


def test_prompt_offers_y_i_n_defaulting_to_check_again(modules, monkeypatch, capsys):
    """The gate passes the exact Choice, default and prompt text."""
    cutting = modules
    _unmerged_single(cutting)
    prompt, calls = _make_prompt("i")
    monkeypatch.setattr(click, "prompt", prompt)

    cutting._check_linked_docs_prs(VERSION)

    text, kwargs = calls[0]
    assert "[i] ignore" in text
    assert "[y] check again" in text
    assert "[n] abort" in text
    assert isinstance(kwargs["type"], click.Choice)
    assert list(kwargs["type"].choices) == ["y", "i", "n"]
    assert kwargs["default"] == "y"
    assert kwargs["show_default"] is True


def test_ignore_dedupes_code_pr_with_several_docs_prs(modules, monkeypatch, capsys):
    """One code PR referencing two unmerged docs PRs is listed once in the
    "shipping" line while the docs PRs are still counted individually."""
    cutting = modules
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(20347)],
        pulls={
            20347: FakePull(
                20347,
                merged_at=datetime(2026, 7, 1),
                body=_back_link(7564) + _back_link(7565),
            )
        },
    )
    docs_repo = FakeRepo(
        pulls={
            7564: FakePull(7564, body=_docs_body(20347), repo="esphome.io"),
            7565: FakePull(7565, body=_docs_body(20347), repo="esphome.io"),
        }
    )
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    prompt, calls = _make_prompt("i")
    monkeypatch.setattr(click, "prompt", prompt)

    cutting._check_linked_docs_prs(VERSION)

    assert len(calls) == 1
    out = capsys.readouterr().out
    assert "Ignoring 2 unmerged docs PR(s); shipping esphome#20347 undocumented" in out
    assert "esphome#20347, esphome#20347" not in out


def test_ignore_lists_every_code_pr_in_order(modules, monkeypatch, capsys):
    """Several code PRs (sharing a docs PR or not) are comma-joined in the order
    they were reported."""
    cutting = modules
    code_repo = FakeRepo(
        milestones=[MILESTONE],
        closed_issues=[FakeIssue(20347), FakeIssue(20348), FakeIssue(20350)],
        pulls={
            20347: FakePull(
                20347, merged_at=datetime(2026, 7, 1), body=_back_link(7564)
            ),
            20348: FakePull(
                20348, merged_at=datetime(2026, 7, 1), body=_back_link(7564)
            ),
            20350: FakePull(
                20350, merged_at=datetime(2026, 7, 1), body=_back_link(7565)
            ),
        },
    )
    docs_repo = FakeRepo(
        pulls={
            7564: FakePull(
                7564, body=_docs_body(20347) + _docs_body(20348), repo="esphome.io"
            ),
            7565: FakePull(7565, body=_docs_body(20350), repo="esphome.io"),
        }
    )
    _wire(cutting, code_repo=code_repo, docs_repo=docs_repo)
    prompt, _ = _make_prompt("i")
    monkeypatch.setattr(click, "prompt", prompt)

    cutting._check_linked_docs_prs(VERSION)

    out = capsys.readouterr().out
    assert (
        "Ignoring 2 unmerged docs PR(s); shipping "
        "esphome#20347, esphome#20348, esphome#20350 undocumented"
    ) in out


def test_ignore_on_second_pass_after_checking_again(modules, monkeypatch, capsys):
    """Answering y re-checks (still unmerged), then i on the next pass lets the cut
    through. Both passes re-fetched and the ignore line is printed once."""
    cutting = modules
    code_repo, docs_repo = _unmerged_single(cutting)
    prompt, calls = _make_prompt("y", "i")
    monkeypatch.setattr(click, "prompt", prompt)

    cutting._check_linked_docs_prs(VERSION)

    assert len(calls) == 2
    assert code_repo.pull_request_calls == [17797, 17797]
    assert docs_repo.pull_request_calls == [7071, 7071]
    out = capsys.readouterr().out
    assert out.count("Ignoring 1 unmerged docs PR(s)") == 1
    assert out.count("Error: Found 1 unmerged docs PR(s)") == 2


def _stub_cut(cutting, monkeypatch, order: list) -> None:
    """Neutralise every heavy cut helper, recording the pre-flight ordering."""
    for name in (
        "_check_open_milestone_prs",
        "_docs_insert_changelog",
        "_docs_update_supporters",
        "_confirm_correct",
        "_create_prs",
        "_ensure_cycle_milestone",
        "_close_cycle_milestone",
        "_mark_cherry_picked",
        "_strategy_cherry_pick",
        "propagate_docs_current_branch",
    ):
        monkeypatch.setattr(cutting, name, lambda *a, **k: [])

    monkeypatch.setattr(
        cutting, "_prompt_base_version", lambda *a, **k: Version.parse("2026.6.0")
    )
    monkeypatch.setattr(
        cutting, "_check_linked_docs_prs", lambda v: order.append(("check", v))
    )
    monkeypatch.setattr(
        cutting, "update_local_copies", lambda: order.append(("update", None))
    )


@pytest.mark.parametrize(
    "cut, version_str",
    [
        ("cut_beta_release", "2026.7.0b2"),
        ("cut_release", "2026.7.1"),
    ],
)
def test_cut_checks_docs_prs_before_touching_local_copies(
    modules, monkeypatch, cut, version_str
):
    """Both entry points gate on docs PRs before any local repo is touched."""
    cutting = modules
    order = []
    _stub_cut(cutting, monkeypatch, order)

    version = Version.parse(version_str)
    getattr(cutting, cut)(version)

    assert order == [("check", version), ("update", None)]
